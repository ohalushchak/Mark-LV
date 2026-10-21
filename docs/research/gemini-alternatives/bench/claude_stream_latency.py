"""Per-turn latency of one long-lived `claude -p --input-format stream-json` process. Usage: python claude_stream_latency.py haiku|sonnet"""
import json, subprocess, time, sys, shutil
exe = shutil.which("claude")
model = sys.argv[1]
p = subprocess.Popen([exe, "-p", "--input-format", "stream-json", "--output-format", "stream-json",
                      "--verbose", "--include-partial-messages", "--model", model, "--tools", "",
                      "--strict-mcp-config", "--setting-sources", "",
                      "--system-prompt", "You are a voice assistant. Answer in one short sentence."],
                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                     text=True, encoding="utf-8", bufsize=1)
t0 = time.monotonic()
for q in ["Привіт! Як справи?", "Скільки буде два плюс три?", "Назви столицю Франції."]:
    s = time.monotonic()
    p.stdin.write(json.dumps({"type": "user", "message": {"role": "user", "content": q}}) + "\n"); p.stdin.flush()
    first = None
    while True:
        line = p.stdout.readline()
        if not line: break
        ev = json.loads(line)
        if ev.get("type") == "stream_event" and first is None:
            d = ev.get("event", {}).get("delta", {})
            if d.get("type") == "text_delta":
                first = time.monotonic() - s
        if ev.get("type") == "result":
            print(f"{model}: first_text={first:.2f}s done={time.monotonic()-s:.2f}s -> {ev.get('result')!r}")
            break
p.stdin.close(); p.wait(timeout=20)
print("startup+3 turns", round(time.monotonic()-t0,2))
