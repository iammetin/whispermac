# WhisperMac Future Option: macOS Input Method

This document captures the long-term architecture idea discussed as "point 7":
turning WhisperMac from an external text-inserting app into a real macOS input
method / input source.

## What This Means

Today WhisperMac works like an external controller:

- WhisperMac records audio
- WhisperMac transcribes locally
- WhisperMac tries to write into the currently focused app
- for corrections it uses Accessibility, simulated keys, paste, and fallbacks

That works well in many normal text fields, but it is fundamentally limited in
custom editors, browser canvases, terminals, and Electron-based UI where the
current text state and selection are not exposed reliably.

The input-method approach would change the model completely:

- macOS gives WhisperMac the active text-input session
- WhisperMac provides provisional text as marked text
- WhisperMac updates that marked text while the user is speaking
- WhisperMac commits final text only when a segment is stable/final

This is much closer to how native dictation and IME-style text systems work.

## Relevant Apple Concepts

- `InputMethodKit`
- `IMKServer`
- `IMKInputController`
- `NSTextInputClient`
- `NSTextInputContext`
- marked text vs. committed text

The key idea is that the target app no longer gets edited "from the outside"
via paste/backspace/selection tricks. Instead, the target app cooperates with
the macOS text input system.

## Why This Could Be Better

- More reliable live correction in many text views
- Better support for provisional text while speaking
- Cleaner separation between mutable live text and committed text
- Less dependence on Accessibility quirks
- Less reliance on blind key simulation in foreign apps

## Important Limitation

This is not a small fix. It is a larger platform-level architecture change.

WhisperMac currently has a Python-first app architecture. A real input method
would require a native macOS component, most likely in Swift or Objective-C,
because the input method layer needs to integrate directly with Apple's text
input system.

## What Would Need To Be Built

1. A native input-method target

- likely based on `InputMethodKit`
- installed as an input source on macOS
- responsible for marked text, live updates, and final commit

2. A connection between the native input method and the existing WhisperMac
   engine

- audio capture / ASR / optional LLM can stay local
- the native layer would ask the engine for live transcript updates
- the engine would return partial and final text

3. A proper session model

- `mutable live text`
- `committed text`
- `final text`

Only the mutable part should remain editable during dictation.

4. Packaging and installation work

- input-source bundle
- signing / permissions
- installation under the user or system input-method location
- switching/activation UX

## Suggested Future Architecture

### Layer 1: Speech Engine

Keep the current local engine responsibilities:

- audio capture
- whisper.cpp inference
- optional live cleanup
- optional LLM correction

### Layer 2: Input Method Bridge

Add a native component that:

- receives partial transcripts
- shows marked text
- updates marked text during speech
- commits stable/final text

### Layer 3: Existing App UI

Keep the current menu-bar app for:

- settings
- model/runtime management
- shortcuts/workflows configuration
- diagnostics/logs

The menu-bar app would become controller/UI, not the primary text writer.

## Expected Benefits Compared To Today

With the current external-writer approach, WhisperMac must guess what the
foreign app supports.

With an input-method approach, WhisperMac would work much closer to the macOS
text system itself. That is the right long-term direction if the goal is:

- reliable live dictation
- reliable live correction
- better behavior across many apps

## Expected Risks / Costs

- Significant implementation effort
- Native macOS code required
- New packaging/distribution path
- More complex debugging and installation
- Some custom apps may still behave differently if they do not cooperate well
  with the text input system

## Recommendation

Do not treat this as the next bugfix.

Treat it as a separate future milestone:

- keep improving the current external-writer architecture pragmatically
- revisit this input-method path when the current approach hits too many hard
  platform limits

## Practical Conclusion

If WhisperMac should eventually behave as close as possible to a native macOS
dictation/input experience across many apps, this input-method architecture is
the most credible long-term route.

For now, it should be considered a dedicated future project, not a quick patch.
