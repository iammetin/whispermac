#!/usr/bin/env python3
"""Generate a whisper.cpp CoreML encoder for a local ggml .bin model.

This follows the official whisper.cpp CoreML path:
1. Convert a supported Whisper model to a CoreML mlpackage
2. Compile it via `xcrun coremlc`
3. Store the result next to the .bin as `<model>-encoder.mlmodelc`

Important limitation:
- This only works for model names supported by the official whisper.cpp
  CoreML conversion script. A random custom/fine-tuned ggml .bin cannot be
  reconstructed from the .bin alone.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, Optional

import coremltools as ct
import torch
import torch.nn.functional as F
import whisper.model
from ane_transformers.reference.layer_norm import LayerNormANE as LayerNormANEBase
from coremltools.models.neural_network.quantization_utils import quantize_weights
from torch import Tensor, nn
from whisper import load_model
from whisper.model import (
    AudioEncoder,
    ModelDimensions,
    MultiHeadAttention,
    ResidualAttentionBlock,
    TextDecoder,
    Whisper,
)


SUPPORTED_MODELS = {
    "tiny",
    "tiny.en",
    "base",
    "base.en",
    "small",
    "small.en",
    "small.en-tdrz",
    "medium",
    "medium.en",
    "large-v1",
    "large-v2",
    "large-v3",
    "large-v3-turbo",
}
MODEL_ALIASES = {
    "large": "large-v3",
    "turbo": "large-v3-turbo",
}
QUANT_SUFFIXES = (
    "_q5_0",
    "_q4_0",
    "_q8_0",
    "_q5_1",
    "_q4_1",
    "_q2_k",
    "_q3_k",
    "_q4_k",
    "_q5_k",
    "_q6_k",
)


# Disable SDPA to stay aligned with the official whisper.cpp converter.
whisper.model.MultiHeadAttention.use_sdpa = False


def _normalized_stem_from_model_bin(model_bin: Path) -> str:
    stem = model_bin.name
    if stem.endswith(".bin"):
        stem = stem[:-4]
    for suffix in QUANT_SUFFIXES:
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    return stem


def derive_upstream_model_name(model_bin: Path) -> Optional[str]:
    stem = _normalized_stem_from_model_bin(model_bin)
    if stem.startswith("ggml-"):
        stem = stem[5:]
    stem = MODEL_ALIASES.get(stem, stem)
    if stem in SUPPORTED_MODELS:
        return stem
    return None


def expected_encoder_path(model_bin: Path) -> Path:
    return model_bin.with_name(_normalized_stem_from_model_bin(model_bin) + "-encoder.mlmodelc")


def linear_to_conv2d_map(state_dict, prefix, local_metadata, strict, missing_keys, unexpected_keys, error_msgs):
    for key in state_dict:
        is_attention = all(substr in key for substr in ["attn", ".weight"])
        is_mlp = any(key.endswith(s) for s in ["mlp.0.weight", "mlp.2.weight"])
        if (is_attention or is_mlp) and len(state_dict[key].shape) == 2:
            state_dict[key] = state_dict[key][:, :, None, None]


def correct_for_bias_scale_order_inversion(state_dict, prefix, local_metadata, strict, missing_keys, unexpected_keys, error_msgs):
    state_dict[prefix + "bias"] = state_dict[prefix + "bias"] / state_dict[prefix + "weight"]
    return state_dict


class LayerNormANE(LayerNormANEBase):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._register_load_state_dict_pre_hook(correct_for_bias_scale_order_inversion)


class MultiHeadAttentionANE(MultiHeadAttention):
    def __init__(self, n_state: int, n_head: int):
        super().__init__(n_state, n_head)
        self.query = nn.Conv2d(n_state, n_state, kernel_size=1)
        self.key = nn.Conv2d(n_state, n_state, kernel_size=1, bias=False)
        self.value = nn.Conv2d(n_state, n_state, kernel_size=1)
        self.out = nn.Conv2d(n_state, n_state, kernel_size=1)

    def forward(self, x: Tensor, xa: Optional[Tensor] = None, mask: Optional[Tensor] = None, kv_cache: Optional[dict] = None):
        q = self.query(x)

        if kv_cache is None or xa is None or self.key not in kv_cache:
            k = self.key(x if xa is None else xa)
            v = self.value(x if xa is None else xa)
        else:
            k = kv_cache[self.key]
            v = kv_cache[self.value]

        wv, qk = self.qkv_attention_ane(q, k, v, mask)
        return self.out(wv), qk

    def qkv_attention_ane(self, q: Tensor, k: Tensor, v: Tensor, mask: Optional[Tensor] = None):
        _, dim, _, seqlen = q.size()
        dim_per_head = dim // self.n_head
        q = q * (float(dim_per_head) ** -0.5)

        mh_q = q.split(dim_per_head, dim=1)
        mh_k = k.transpose(1, 3).split(dim_per_head, dim=3)
        mh_v = v.split(dim_per_head, dim=1)

        mh_qk = [torch.einsum("bchq,bkhc->bkhq", [qi, ki]) for qi, ki in zip(mh_q, mh_k)]
        if mask is not None:
            for head_idx in range(self.n_head):
                mh_qk[head_idx] = mh_qk[head_idx] + mask[:, :seqlen, :, :seqlen]

        attn_weights = [aw.softmax(dim=1) for aw in mh_qk]
        attn = [torch.einsum("bkhq,bchk->bchq", wi, vi) for wi, vi in zip(attn_weights, mh_v)]
        attn = torch.cat(attn, dim=1)
        return attn, torch.cat(mh_qk, dim=1).float().detach()


class ResidualAttentionBlockANE(ResidualAttentionBlock):
    def __init__(self, n_state: int, n_head: int, cross_attention: bool = False):
        super().__init__(n_state, n_head, cross_attention)
        self.attn = MultiHeadAttentionANE(n_state, n_head)
        self.attn_ln = LayerNormANE(n_state)
        self.cross_attn = MultiHeadAttentionANE(n_state, n_head) if cross_attention else None
        self.cross_attn_ln = LayerNormANE(n_state) if cross_attention else None

        n_mlp = n_state * 4
        self.mlp = nn.Sequential(
            nn.Conv2d(n_state, n_mlp, kernel_size=1),
            nn.GELU(),
            nn.Conv2d(n_mlp, n_state, kernel_size=1),
        )
        self.mlp_ln = LayerNormANE(n_state)


class AudioEncoderANE(AudioEncoder):
    def __init__(self, n_mels: int, n_ctx: int, n_state: int, n_head: int, n_layer: int):
        super().__init__(n_mels, n_ctx, n_state, n_head, n_layer)
        self.blocks = nn.ModuleList([ResidualAttentionBlockANE(n_state, n_head) for _ in range(n_layer)])
        self.ln_post = LayerNormANE(n_state)

    def forward(self, x: Tensor):
        x = F.gelu(self.conv1(x))
        x = F.gelu(self.conv2(x))
        assert x.shape[1:] == self.positional_embedding.shape[::-1], "incorrect audio shape"
        x = (x + self.positional_embedding.transpose(0, 1)).to(x.dtype).unsqueeze(2)
        for block in self.blocks:
            x = block(x)
        x = self.ln_post(x)
        x = x.squeeze(2).transpose(1, 2)
        return x


class TextDecoderANE(TextDecoder):
    def __init__(self, n_vocab: int, n_ctx: int, n_state: int, n_head: int, n_layer: int):
        super().__init__(n_vocab, n_ctx, n_state, n_head, n_layer)
        self.blocks = nn.ModuleList(
            [ResidualAttentionBlockANE(n_state, n_head, cross_attention=True) for _ in range(n_layer)]
        )
        self.ln = LayerNormANE(n_state)


class WhisperANE(Whisper):
    def __init__(self, dims: ModelDimensions):
        super().__init__(dims)
        self.encoder = AudioEncoderANE(
            self.dims.n_mels,
            self.dims.n_audio_ctx,
            self.dims.n_audio_state,
            self.dims.n_audio_head,
            self.dims.n_audio_layer,
        )
        self.decoder = TextDecoderANE(
            self.dims.n_vocab,
            self.dims.n_text_ctx,
            self.dims.n_text_state,
            self.dims.n_text_head,
            self.dims.n_text_layer,
        )
        self._register_load_state_dict_pre_hook(linear_to_conv2d_map)


def convert_encoder(hparams, model, quantize: bool = False):
    model.eval()
    input_shape = (1, hparams.n_mels, 3000)
    input_data = torch.randn(input_shape)
    traced_model = torch.jit.trace(model, input_data)
    model = ct.convert(
        traced_model,
        convert_to="mlprogram",
        inputs=[ct.TensorType(name="logmel_data", shape=input_shape)],
        outputs=[ct.TensorType(name="output")],
        compute_units=ct.ComputeUnit.ALL,
    )
    if quantize:
        model = quantize_weights(model, nbits=16)
    return model


def generate_encoder_mlpackage(model_name: str, output_path: Path, optimize_ane: bool = True, quantize: bool = False) -> None:
    whisper_model = load_model(model_name).cpu()
    hparams = whisper_model.dims

    if optimize_ane:
        whisper_ane = WhisperANE(hparams).eval()
        whisper_ane.load_state_dict(whisper_model.state_dict())
        encoder = whisper_ane.encoder
    else:
        encoder = whisper_model.encoder

    encoder_model = convert_encoder(hparams, encoder, quantize=quantize)
    encoder_model.save(str(output_path))


def compile_mlpackage(mlpackage_path: Path, output_dir: Path) -> Path:
    subprocess.run(
        ["xcrun", "coremlc", "compile", str(mlpackage_path), str(output_dir)],
        check=True,
    )
    return output_dir / (mlpackage_path.stem + ".mlmodelc")


def remove_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
    elif path.is_dir():
        shutil.rmtree(path, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-bin", required=True, help="Path to the local ggml .bin model")
    parser.add_argument("--output-dir", help="Directory for generated CoreML artifacts")
    parser.add_argument("--quantize", action="store_true", help="Quantize CoreML weights to F16")
    args = parser.parse_args()

    model_bin = Path(args.model_bin).expanduser().resolve()
    if not model_bin.is_file():
        raise FileNotFoundError(f"Whisper-Modell nicht gefunden: {model_bin}")

    expected_encoder = expected_encoder_path(model_bin)
    if expected_encoder.is_dir():
        print(f"CoreML-Encoder bereits vorhanden: {expected_encoder}")
        return 0

    model_name = derive_upstream_model_name(model_bin)
    if not model_name:
        raise RuntimeError(
            "Kein automatischer CoreML-Build fuer dieses Modell moeglich. "
            f"Unterstuetzt sind nur offizielle Whisper-Modelle: {', '.join(sorted(SUPPORTED_MODELS))}"
        )

    output_dir = Path(args.output_dir).expanduser().resolve() if args.output_dir else model_bin.parent
    output_dir.mkdir(parents=True, exist_ok=True)

    mlpackage_path = output_dir / f"coreml-encoder-{model_name}.mlpackage"
    compiled_path = output_dir / f"coreml-encoder-{model_name}.mlmodelc"

    remove_path(mlpackage_path)
    remove_path(compiled_path)
    remove_path(expected_encoder)

    last_error: Optional[Exception] = None
    for optimize_ane in (True, False):
        try:
            print(f"Erzeuge CoreML-Encoder fuer {model_name} (optimize_ane={optimize_ane})")
            generate_encoder_mlpackage(model_name, mlpackage_path, optimize_ane=optimize_ane, quantize=args.quantize)
            built_dir = compile_mlpackage(mlpackage_path, output_dir)
            remove_path(expected_encoder)
            built_dir.rename(expected_encoder)
            remove_path(mlpackage_path)
            print(f"Fertig: {expected_encoder}")
            return 0
        except Exception as exc:
            last_error = exc
            print(f"CoreML-Encoder-Generierung fehlgeschlagen (optimize_ane={optimize_ane}): {exc}", file=sys.stderr)
            remove_path(mlpackage_path)
            remove_path(compiled_path)

    raise RuntimeError(f"CoreML-Encoder konnte nicht erzeugt werden: {last_error}")


if __name__ == "__main__":
    raise SystemExit(main())
