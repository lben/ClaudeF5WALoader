# WALoader LLM authoring kit (operator notes)

**Audience: the WALoader operator/admin** (this file is the only one NOT meant
to be fed to the LLM).

This folder turns a general-purpose coding LLM into a WALoader-native app
builder: from the very first user message ("I want a clients dashboard") the
LLM already knows the app is a Streamlit app deployed on WALoader, styled per
your design language, backed by Dataset Concepts, shipped with tests, and
delivered as a single markdown bundle.

## How to wire it into the LLM

1. **`SYSTEM_PROMPT.md`** → paste its contents into the assistant's *system
   prompt / custom instructions* slot.
2. **`01-building-waloader-apps.md`**, **`02-previews.md`**,
   **`03-help-and-faq.md`** → attach as project files / knowledge documents so
   the assistant can read them (they are written to be read by the LLM, not by
   end users).
3. **`DESIGN_LANGUAGE.md`** → add YOUR design-language file next to them,
   under exactly that name. The kit references it everywhere but does not
   ship it; without it the assistant is instructed to ask for it or fall back
   to clean Streamlit defaults.

That's it — no WALoader-side configuration. End users never see these files;
they just chat.

## Keeping the kit truthful

`tests/test_authoring_kit.py` pins the facts in these files to the platform's
real defaults (upload limits, dataset formats, the bundle format markers). If
you change platform defaults or the bundle contract, the test suite fails
until the kit is updated — same doc-sync discipline as `docs/configuration.md`.

If your instance runs with non-default limits, edit the numbers in
`03-help-and-faq.md` to match your `config/waloader.toml` (and adjust the test
expectations consciously).

## Relationship to the older flow

`docs/llm-bundle-prompt.md` (the paste-one-prompt-at-the-end template) is
superseded by this kit and now just points here. It remains usable as a
fallback when someone is stuck with an un-primed LLM mid-conversation.
