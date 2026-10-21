# Alternatives to Gemini Live — research notes (parked)

_Collected 2026-10-16. Parked by the user to return to later; nothing here is implemented._

## Why

Testing showed two problems with the current Gemini setup:

- **Model quality:** wrong tool picks, repeated actions after an interruption, blind click chains (the Recycle Bin case).
- **Request limits:** in one session's log every REST rung of the VISION ladder failed:
  - 3.6-flash and 3.5-flash: out of quota;
  - flash-latest and 3.5-flash-lite: not answering;
  - 2.5-flash: not available to the key.

  So `screen_click` and `computer_use` fell to the Live rung and took 10–60 s.

The user already pays for Claude Code and Codex subscriptions.

## Options weighed

| Option | Verdict |
|---|---|
| Claude Code / Codex as the brain, Gemini only for STT/TTS | Works, but three network hops (est. 3–6 s to first sound). Gemini has no transcription-only mode with an AI Studio key: Live always answers, and batch transcription is ~1–2 s. |
| Claude as one more rung in `core/gemini.py` (one-shot calls: vision, computer use, file reading) | **Best quick win.** Small change, fixes quota and quality where it hurts most; these calls are not latency-critical. |
| Live stays the voice; complex tasks handed to Claude in the background (announced like agents) | Good middle step. Live keeps speed and barge-in, Claude does the hard thinking. |
| Several Google keys from several accounts, rotated | **Rejected:** Google APIs ToS §2(d) forbids circumventing limits; risks the accounts; free-tier data may be used for training (screenshots, voice). Limits are per project, not per key. A paid tier on one key is the legitimate route. |
| Fully local (LM Studio + local STT/TTS) | Feasible; Gemma 4 12B routes simple commands well but answers in 3–15 s, and multi-step / vision grounding are doubtful at 12B. |
| **Local STT/TTS + Claude Code (or Codex) as the brain** | **Preferred target.** No Google key needed at all (Claude's built-in WebSearch can replace the SEARCH tier). Est. ~1.7–4 s from end of speech to first sound. |

## Measurements (this machine: RTX 5080 Laptop 16 GB, Windows 11)

**Brains**

| What | Result |
|---|---|
| `claude -p`, new process per request, haiku, no tools | 2.7–4 s total (API ~0.8–2.5 s) |
| One long-lived `claude -p --input-format stream-json` (haiku / sonnet) | first text 0.8–2.9 s per turn |
| `codex exec`, new process per request | answer at 7–8 s; `codex app-server` (persistent) not measured |
| Gemma 4 12B QAT, LM Studio, real tools (25) + `core/prompt.txt` (~11k tokens) | 7/8 correct tool picks (8th asked back — correct without context); 2.7–15 s per answer, thinking on; ~9.3 GB VRAM at 32k context |
| Qwen 3.5 9B, Ollama | 5/8 correct; first call 79 s |

With a 400-token cap Gemma once answered "Очищаю корзину… завершена" **without calling any tool** (in Russian). This is the failure mode to guard against with any small model.

**TTS (Ukrainian)**

| Engine | First audio | Notes |
|---|---|---|
| `tts_uk` (RAD-TTS++ + Vocos), CUDA; voices Tetiana / Lada / Mykyta | **90–170 ms per whole sentence**; load ~22 s (keep it loaded) | best sounding. **Silently drops Latin words**; Cyrillic spellings ("Клод Код", "Нотпед", "Телеграм") are pronounced right. Needs the patriotyk vocos fork plus a patch (below). |
| Piper `uk_UA-ukrainian_tts-medium`, CPU | ~110 ms | needs lowercased text (no uppercase phonemes); drops Latin words |
| Piper `uk_UA-lada-x_low`, CPU | ~75 ms | worst quality, but reads Latin ("Слак") |

So a TTS front needs an **English → Cyrillic spelling dictionary** for app names and terms (Slack → «Слек», Notepad → «Нотпед»), later maybe automatic transliteration.

**STT (Ukrainian, real voice, 8 typical commands)**

- **Energy end-of-speech is unusable.** A simple RMS threshold never fired in a real room, so every take ran to the 15 s cap. **Silero VAD** found every phrase correctly; it is mandatory.
- On speech cut by Silero, both models got 4–5 of 8 commands fully right, the rest with small errors. ~0.2–0.4 s per phrase.
  - **Whisper large-v3-turbo** (CUDA fp16, `language="uk"`): best on names; never switches language.
  - **Parakeet TDT 0.6B v3** (int8, CPU, via `onnx-asr`):
    - writes Cyrillic names and words-for-numbers (nicer for TTS);
    - cannot force the language: on unsegmented audio it produced English gibberish, so it is usable only after VAD.
  - Whisper large-v3 is slightly better than turbo at twice the time.
- **"Claude Code" was not recognised by either model** ("код", "код код", "Cloud Code"). Whisper `hotwords` helps sometimes and hurts sometimes ("Запустий", "Slackу"), so it should stay off. A Claude brain can infer the meaning from context.
- **Mic is quiet:** peak ~0.03 of full scale; raising Windows input gain should help any engine.
- **Recommendation:** Silero VAD + Whisper turbo by default; Parakeet as a setting.

**Latency budget, local voice + Claude**

| Step | Time |
|---|---|
| End-of-speech pause (Silero) | ~0.6 s (tunable) |
| STT | 0.2–0.4 s |
| Claude first text (warm session) | 0.8–2.9 s |
| First TTS sentence (`tts_uk`) | 0.1–0.2 s |
| **Total, end of speech → first sound** | **~1.7–4 s** (Live today: ~1 s) |

## Facts from the docs (checked 2026-10-16)

**Claude Code**

- **Long-lived process:**
  - `--input-format stream-json --output-format stream-json --verbose --include-partial-messages` keeps one process across turns.
  - A user turn is one stdin line: `{"type":"user","message":{"role":"user","content":...},"parent_tool_use_id":null}`.
  - Text deltas arrive as `stream_event` / `text_delta`, and each turn ends with `result`.
  - Interrupt: a control request (`interrupt`) works in streaming input mode. The exact raw JSON is unconfirmed; SIGINT is unreliable on Windows.
- **Lockdown:**
  - `--tools ""` removes the built-ins; `--strict-mcp-config` plus `--mcp-config` limit tools to our servers.
  - An HTTP MCP server needs `"type":"http"`; add `alwaysLoad: true` to skip the ToolSearch round trip.
  - `--system-prompt` replaces the default prompt.
  - `--setting-sources ""` is accepted (verified here).
  - `--bare` needs an API key, so it does not work with the subscription.
- **Model and caching:**
  - Images returned by MCP tools are seen by Claude.
  - Prompt cache lasts 1 h on a subscription.
  - Thinking cannot be turned off on the 5.5 models; use `--effort low`.
- **Terms:** one person running the unmodified CLI on their own subscription looks tolerated. A public fork whose users sign in through it is a grey area (Anthropic points products to API keys). Make the mode opt-in.

**Codex**

- `codex app-server` JSON-RPC: `initialize` → `thread/start` → `turn/start` (text and images) → `item/agentMessage/delta` → `turn/completed`; `turn/interrupt`.
- MCP over HTTP: `[mcp_servers.x] url=…`.
- `features.shell_tool=false` turns off shell. Unclear: disabling apply_patch and view_image, whether MCP image results reach the model, and `-c` syntax for MCP servers. So Codex comes after Claude.

**Gemini**

- TTS models: `gemini-3.8-flash(-lite)-tts`; streaming through the Interactions API; 24 kHz PCM; same 30 voices as Live.
- STT: no transcription-only mode with an AI Studio key.
- Cloud Speech-to-Text probably does not take an AI Studio key.

## Forks (742 forks of FatihMakes/Mark-LV scanned, 58 diverged, 17 read)

No fork uses Claude Code / Codex as the main brain, and none does Ukrainian speech. Worth borrowing:

| What | Where |
|---|---|
| **A class that mimics the Live session** (`send_realtime_input`, `send_client_content`, `send_tool_response`, `receive()` yielding Gemini-shaped objects), so `main.py`'s mic, playback, HUD, barge-in and tool dispatch stay as they are. For Claude: an MCP server inside the adapter turns Claude's tool calls into Gemini-shaped `tool_call`s and returns `send_tool_response` to Claude. | [BlackStarG5 `core/local_session.py`](https://github.com/BlackStarG5/Mark-LIV/blob/main/core/local_session.py), [senthuran-ukr/ada `core/openai_realtime.py`](https://github.com/senthuran-ukr/ada/blob/main/core/openai_realtime.py) |
| Upstream's own local cascade ("Mark XL", June 2026): VAD, faster-whisper / Vosk, sentence-streamed LLM, Edge / Kokoro TTS, Gemini → OpenAI tool schemas. Upstream still ships `core/stt.py`, `core/tts.py`, `core/llm_client.py` unused; this fork deleted them. | [FatihMakes/Mark-LV @ 5d8d214](https://github.com/FatihMakes/Mark-LV/blob/5d8d214/main.py) |
| Sentence streaming to TTS, warm-ups, hardened `transcribe` (`no_speech_prob`, hallucinated-hint check) | BlackStarG5 `_model_reply`, `_warm_*`, `LocalSpeech.transcribe` |
| `AudioOutput` (interrupt within ~90 ms), `_pop_sentence`, `sanitize_for_tts`, barge-in only after 3 transcribed words | [Giljacob67 `pipecat-poc`](https://github.com/Giljacob67/Jarvis-Mark-XL/blob/pipecat-poc/poc/bot.py) and its gen-1 `core/` |
| Language reminder after each tool result (models drift to English) | villysavescu `_LANG_REMINDER_MSG` |
| Turn counter that drops stale TTS after barge-in; per-turn latency marks | bfrpaulondev/Mark-BP `core/voice_runtime.py` |
| `core/gemini.py` shim with the same `call`/`text`/`as_json` on another backend | Guigui357 `core/gemini.py` |
| Loop guard (same tool over and over) | maseko-lucky-9 `loop_guard` (idea only) |
| Warm Agent-SDK session | [omygod9138 `actions/ask_claude.py`](https://github.com/omygod9138/Mark-L/blob/main/actions/ask_claude.py) (without its `bypassPermissions`) |

**Avoid:**
- a `confirm` parameter written by the model (Giljacob67);
- Robin-liv's browser bridge with `Access-Control-Allow-Origin: *` and no token;
- dragonballls' API-key scraping from a logged-in browser and its 10-minute auto-pull;
- Pipecat wholesale (it would replace our EchoGuard, wake word and barge-in path).

## Plan when resuming

1. **Claude rung in `core/gemini.py`** for one-shot calls (vision for `screen_click` and `computer_use`, `file_processor`, `code_helper`…).
   - Run it as a warm `claude -p` stream-json process, haiku or sonnet with low effort.
   - Add a setting for whether the subscription goes first or only as fallback, because the limits are shared with the user's coding.
2. **New voice mode behind a switch** (Gemini Live stays the default and the fallback): a Live-session adapter with
   - Silero VAD;
   - Whisper turbo (Parakeet optional);
   - a warm Claude session whose MCP bridge feeds `_execute_tool`;
   - `tts_uk` per sentence, with the English → Cyrillic dictionary.

   Also rework the 9 inline tools that depend on the Live session (`screen_process` returns the image as a tool result), feed `core/announce.py` items to the brain, and feed `core/activity.py` from the transcripts.
3. **Codex through `app-server`** once its built-in tools can be locked down.

**Open questions:**
- whether the user likes the `tts_uk` voices (samples were sent; the fallback is Gemini TTS at +0.5–1 s);
- what to do about contact names (fuzzy matching in `send_message`);
- a separate brain model and effort setting next to the coding agent's in the AGENT panel.

## Reproducing the measurements

The scripts are in `bench/`:
- `claude_stream_latency.py`
- `local_llm_tool_routing.py` (LM Studio on :1234, or Ollama's `/v1`)
- `tts_bench.py`
- `stt_bench.py`
- `mic_test.py` (fix its end-of-speech with Silero first)

The environment lived in `%TEMP%\voicebench\venv`, Python 3.12. To rebuild it:

```
python -m venv venv
venv\Scripts\python -m pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu128
venv\Scripts\python -m pip install numpy tts-uk faster-whisper nvidia-cublas-cu12 nvidia-cudnn-cu12 piper-tts "onnx-asr[cpu,hub]" soundfile sounddevice librosa
venv\Scripts\python -m pip install --force-reinstall --no-deps git+https://github.com/patriotyk/vocos.git
venv\Scripts\python -m piper.download_voices --download-dir piper_voices uk_UA-ukrainian_tts-medium uk_UA-lada-x_low
```

Setup notes:
- **Vocos patch:** `vocos/feature_extractors.py` `MelSpectrogramFeatures.__init__` must accept `f_min`, `f_max`, `norm` and `mel_scale` and pass them to `torchaudio.transforms.MelSpectrogram`, because the tts_uk vocoder config uses them.
- **CUDA DLLs:** faster-whisper needs `site-packages\nvidia\cublas\bin` and `...\cudnn\bin` on `PATH`.
- **PyTorch:** the 5080 (sm_120) needs the cu128 build. The project's own torch is CPU-only.
- **First run downloads:** `tts_uk` downloads its models into `./models` on import, and the Silero VAD comes via `onnx_asr.load_vad("silero")`.
