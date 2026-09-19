"""One-time, secret-free production migration."""
from pathlib import Path
import json
p=Path('scripts/fastnews247_mvp.py')
s=p.read_text(encoding='utf-8')
s=s.replace('import time\n','import time\nimport tempfile\nfrom contextlib import contextmanager\n')
s=s.replace('    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")','''    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)''')
s=s.replace('def strip_urls(value: str) -> str:\n', '''def strip_urls(value: str) -> str:
    # Preserve domain-shaped brand entities without generating Telegram links.
    value = re.sub(r"(?i)(?<![/\\w])crypto\\.com\\b", "Crypto chấm com", value or "")
''')
s=s.replace('"User-Agent": "FastNews247/0.1 (+Telegram news channel MVP)",','"User-Agent": "TinNhanh247/1.0",\n            "Cache-Control": "no-cache",\n            "Pragma": "no-cache",')
s=s.replace('    with urllib.request.urlopen(request, timeout=timeout) as response:\n        return response.read()', '''    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read(4_000_001)
        if len(raw) > 4_000_000:
            raise ValueError("response-too-large")
        FETCH_AUDIT[url] = {"httpDate": response.headers.get("Date"),
                            "age": response.headers.get("Age"),
                            "lastModified": response.headers.get("Last-Modified"),
                            "cache": response.headers.get("X-Cache"),
                            "sha256": hashlib.sha256(raw).hexdigest(),
                            "fetchedAt": dt.datetime.now(dt.timezone.utc).isoformat()}
        return raw''')
s=s.replace('def fetch_url(', 'FETCH_AUDIT = {}\n\n\ndef fetch_url(',1)
s=s.replace('"{http://www.w3.org/2005/Atom}published"])','"{http://www.w3.org/2005/Atom}published", "{http://www.w3.org/2005/Atom}updated", "{http://purl.org/dc/elements/1.1/}date"])')
start=s.index('def parse_time('); end=s.index('\n\nclass ArticleHTMLParser',start)
s=s[:start]+'''def parse_time(value: str) -> float:
    if not value:
        return 0
    for parser in (email.utils.parsedate_to_datetime,
                   lambda v: dt.datetime.fromisoformat(v.replace("Z", "+00:00"))):
        try:
            parsed = parser(value)
            # Never silently interpret publisher dates using the host timezone.
            if parsed.tzinfo is not None:
                return parsed.timestamp()
        except (ValueError, TypeError, OverflowError):
            pass
    return 0
'''+s[end:]
s=s.replace('    if "#BTC" in tags:\n        found.append(("₿", "Crypto"))\n    if "#OIL" in tags:\n        found.append(("🛢", "Dầu"))\n    if "#XAUUSD" in tags:\n        found.append(("🥇", "Vàng"))\n','')
s=s.replace('"cpi", "ppi", "nfp", "nonfarm", "payrolls", "jobless", ', '"nfp", "nonfarm", ')
s=s.replace('"saudi", "opec", "riyadh"','"saudi", "riyadh"').replace('"brazilian", "real", "brl"','"brazilian", "brl"')
s=s.replace('("🇷🇺🇺🇦", "Nga-Ukraine", ["russia", "ukraine"]),','("🇷🇺", "Nga", ["russia"]),\n        ("🇺🇦", "Ukraine", ["ukraine"]),')
s=s.replace('#Fastnews','#Tinnhanh247').replace('FASTNEWS 247','Tin nhanh 247').replace('Fast News 247','Tin nhanh 247')
s=s.replace('return [part.strip() for part in parts if len(part.split()) >= 7 and not re.search(r"[,;:\\-–—]\\s*$", part)]','return [part.strip() for part in parts if len(part.split()) >= 7 and re.search(r\'[.!?][”"’]?$\', part) and "..." not in part and "…" not in part]')
s=s.replace('    title = title.rstrip(" .")','''    if "..." in title or "…" in title:
        return "", ""
    if not _fact_numbers(title).issubset(_fact_numbers(source_title)):
        return "", ""
    title = strip_urls(title).rstrip(" .")
    item["source_sentences"] = source_summary_sentences(item)''')
s=s.replace('        sentence = sentence.strip()','        sentence = strip_urls(sentence).strip()')
s=s.replace('    issues = []\n    sentences = split_complete_sentences(summary)','    issues = []\n    if "..." in summary or "…" in summary or not re.search(r\'[.!?][”"’]?$\', summary):\n        issues.append("incomplete-summary")\n    sentences = split_complete_sentences(summary)')
# Remove unsupported/plaintext-token publisher entirely; keep existing configured bridge.
start=s.index('    token = os.environ.get(tg.get("tokenEnv", ""))')
end=s.index('\n\ndef assert_posting_ready',start)
s=s[:start]+'''    raise RuntimeError("Only the credential-owning OpenClaw bridge is supported.")


def telegram_bridge_post(tg: dict, text: str) -> dict:
    channel_id = tg.get("channelId", "")
    if not channel_id:
        raise RuntimeError("Missing Telegram channelId")
    cli = Path(os.environ.get("APPDATA", "")) / "npm/node_modules/openclaw/openclaw.mjs"
    command = ["node", str(cli), "message", "send", "--channel=telegram",
               f"--target={channel_id}", f"--message={text}", "--json"]
    try:
        result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=60)
    except subprocess.TimeoutExpired:
        return {"status": "pending", "reason": "bridge-timeout"}
    # Logs, exit status and submission are NOT an API acknowledgement.
    decoder = json.JSONDecoder()
    for index, char in enumerate(result.stdout):
        if char != "{":
            continue
        try:
            payload, _ = decoder.raw_decode(result.stdout[index:])
        except ValueError:
            continue
        ack = extract_ack(payload)
        if ack:
            return {"status": "confirmed", **ack}
    return {"status": "pending", "reason": "bridge-no-message-ack",
            "exitCode": result.returncode}


def extract_ack(payload):
    if not isinstance(payload, dict) or payload.get("ok") is False:
        return None
    message_id = payload.get("messageId", payload.get("message_id"))
    chat_id = payload.get("chatId") or (payload.get("chat") or {}).get("id")
    if str(message_id or "").isdigit() and int(message_id) > 0 and chat_id:
        return {"messageId": int(message_id), "chatId": str(chat_id)}
    for key in ("result", "payload", "data"):
        ack = extract_ack(payload.get(key))
        if ack:
            return ack
    return None
'''+s[end:]
start=s.index('    token_env =', s.index('def assert_posting_ready'))
end=s.index('\n\ndef test_telegram',start)
s=s[:start]+'    raise RuntimeError("Unsupported credential transport; use OpenClaw bridge.")\n'+s[end:]
s=s.replace('    telegram_post(config, message)\n    print("Telegram test message sent.")\n    return 0','    result = telegram_post(config, message)\n    print(json.dumps(result))\n    return 0 if result and result.get("status") == "confirmed" else 2')
s=s.replace('if value.get("time", 0) >= cutoff','if value.get("time", 0) >= cutoff or value.get("status", "legacy-unconfirmed") != "confirmed"')
s=s.replace('    return {"seen": seen}','    return {**state, "schemaVersion": 2, "seen": seen}')
s=s.replace('    rejected = []','    rejected = []\n    feed_audit = []',1)
s=s.replace('            for item in feed_items:','''            dates = [parse_time(i["published"]) for i in feed_items]
            feed_audit.append({"source": feed["name"], "items": len(feed_items),
                               "latestPublished": max(dates, default=0),
                               "fresh": sum(not freshness_issue(i, config) for i in feed_items),
                               "http": FETCH_AUDIT.get(feed["url"], {})})
            for item in feed_items:''')
s=s.replace('    for item in candidates:\n', '''    for item in candidates:
        if any(same_event(item, previous) for previous in list(state["seen"].values()) + selected):
            rejected.append(f"{item['source']}: duplicate-event :: {item['title']}")
            continue
''',1)
start=s.index('                try:\n                    delivery = telegram_post')
end=s.index('\n    output_path.parent',start)
s=s[:start]+'''                record = {"time": time.time(), "title": item["title"], "source": item["source"],
                          "score": item["score"], "link": item.get("link", ""),
                          "status": "pending", "reason": "write-ahead-send-intent"}
                state["seen"][item["fingerprint"]] = record
                save_json(state_path, state)
                try:
                    delivery = telegram_post(config, draft)
                    record.update(delivery or {"status": "pending", "reason": "missing-ack"})
                except Exception:
                    record.update(status="pending", reason="publisher-exception-uncertain")
                save_json(state_path, state)
                print("Delivery: " + json.dumps(record, ensure_ascii=False), flush=True)

    save_json(ROOT / "outputs/fastnews247/last_run.json",
              {"at": dt.datetime.now(dt.timezone.utc).isoformat(), "post": post,
               "feeds": feed_audit, "errors": errors, "rejections": rejected,
               "selected": selected,
               "deliveries": [v for v in state["seen"].values() if v.get("time", 0) >= run_started]})
'''+s[end:]
s=s.replace('    config = json.loads(json.dumps(config))\n    config["posting"]["telegram"]["enabled"] = bool(post)','    run_started = time.time()\n    config = json.loads(json.dumps(config))\n    config["posting"]["telegram"]["enabled"] = bool(post)')
s=s.replace('    return 0\n\n\ndef main()', '    return 2 if post and any(v.get("status") == "pending" and v.get("time", 0) >= run_started for v in state["seen"].values()) else (1 if len(errors) == len(config["feeds"]) else 0)\n\n\ndef main()')
s=s.replace('        return run_once(config, post=args.post)','''        try:
            with owned_lock(ROOT / "storage/fastnews247/worker.lock"):
                return run_once(config, post=args.post)
        except BlockingIOError:
            print("Skipped: another process owns the news lock.")
            return 0''')
insert=s.index('\ndef main()')
s=s[:insert]+'''
def canonical_url(url):
    parsed = urllib.parse.urlsplit(url)
    return parsed.netloc.lower().removeprefix("www.") + parsed.path.rstrip("/")


def same_event(first, second):
    if first.get("link") and second.get("link") and canonical_url(first["link"]) == canonical_url(second["link"]):
        return True
    a, b = _meaningful_terms(first.get("title", "")), _meaningful_terms(second.get("title", ""))
    return bool(a and b and (a == b or
                (len(a & b) >= 4 and len(a & b) / min(len(a), len(b)) >= 0.8
                 and _fact_numbers(first.get("title", "")) == _fact_numbers(second.get("title", "")))))


@contextmanager
def owned_lock(path):
    # OS byte-range lock: atomic ownership, released by kernel on crash.
    # Never unlink this inode; age-based deletion allows concurrent owners.
    path.parent.mkdir(parents=True, exist_ok=True)
    stream = open(path, "a+b")
    try:
        stream.seek(0, 2)
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        if os.name == "nt":
            import msvcrt
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise BlockingIOError("busy") from exc
        else:
            import fcntl
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        stream.close()

'''+s[insert:]
p.write_text(s,encoding='utf-8')
c=Path('config/fastnews247.sources.json')
config=json.loads(c.read_text(encoding='utf-8')); config['channel']['name']='Tin nhanh 247'
c.write_text(json.dumps(config,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
