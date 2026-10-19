"""Live code: only real changes, diffed against what was there before."""

from types import SimpleNamespace

from firedev.codefeed import CodeFeed, lang_of, regions


def test_regions_carry_the_real_added_lines_with_context():
    before = ["a", "b", "c", "d"]
    after = ["a", "b", "NEW1", "NEW2", "c", "D!"]
    r = regions(before, after)
    assert [x["added"] for x in r] == [["NEW1", "NEW2"], ["D!"]]
    assert r[0]["at"] == 2 and r[0]["before"] == ["a", "b"] and r[1]["removed"] == ["d"]
    assert regions(before, before) == []


def test_languages():
    assert lang_of("site/styles.css") == "css" and lang_of("index.html") == "html" and lang_of("logo.png") is None


class FakeRemote:
    connected = True

    def __init__(self, files, head=None):
        self.files, self.head = files, head or {}

    async def read_bytes(self, path, limit=0):
        return self.files[path].encode()

    async def run(self, cmd, timeout=0):
        name = cmd.split("HEAD:")[1].split(" ")[0].strip("'")
        return (0, self.head.get(name, ""), "")


def session(remote, git=True):
    events = []
    s = SimpleNamespace(project=SimpleNamespace(id="p"), path="/w", remote=remote, is_git=git,
                        emit=lambda t, tid, **d: events.append((t, d)))
    return s, events


async def test_capture_replays_only_what_changed():
    remote = FakeRemote({"/w/index.html": "<h1>Hi</h1>\n"})
    s, events = session(remote)
    feed = CodeFeed()
    await feed.capture(s, "t1", "index.html", "add")
    assert events[-1][1]["regions"][0]["added"] == ["<h1>Hi</h1>"] and events[-1][1]["known"]
    remote.files["/w/index.html"] = "<h1>Hi</h1>\n<p>More</p>\n"
    await feed.capture(s, "t1", "index.html", "update")
    assert events[-1][1]["regions"][0]["added"] == ["<p>More</p>"] and events[-1][1]["regions"][0]["before"] == ["<h1>Hi</h1>"]
    n = len(events)
    await feed.capture(s, "t1", "index.html", "update")
    assert len(events) == n, "a save without a change replays nothing"


async def test_an_edit_without_a_known_before_is_shown_not_typed():
    s, events = session(FakeRemote({"/w/app.js": "let a = 1;\n"}), git=False)
    await CodeFeed().capture(s, None, "app.js", "update")
    d = events[-1][1]
    assert d["known"] is False and d["regions"][0]["added"] == [] and d["regions"][0]["after"] == ["let a = 1;"]


async def test_a_tracked_file_diffs_against_git_head():
    s, events = session(FakeRemote({"/w/a.css": "x{}\ny{}\n"}, head={"a.css": "x{}\n"}))
    await CodeFeed().capture(s, None, "a.css", "update")
    assert events[-1][1]["regions"][0]["added"] == ["y{}"]


class ShellRemote:
    """Runs the real shell scripts on this machine."""
    connected = True

    async def run(self, cmd, timeout=0):
        import asyncio as aio
        p = await aio.create_subprocess_exec("bash", "-c", cmd, stdout=aio.subprocess.PIPE, stderr=aio.subprocess.PIPE)
        out, err = await p.communicate()
        return p.returncode, out.decode(), err.decode()

    async def read_bytes(self, path, limit=0):
        with open(path, "rb") as f:
            return f.read()


async def test_a_task_replays_only_its_own_edits_not_everything_since_the_last_commit(tmp_path):
    (tmp_path / "site").mkdir()
    page = tmp_path / "site" / "index.html"
    page.write_text("<h1>Old</h1>\n" + "".join(f"<p>{i}</p>\n" for i in range(50)))
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "x.js").write_text("ignored\n")
    events = []
    s = SimpleNamespace(project=SimpleNamespace(id="p"), path=str(tmp_path), remote=ShellRemote(), is_git=True,
                        emit=lambda t, tid, **d: events.append(d))
    feed = CodeFeed()
    feed.prime(s)
    await feed.priming["p"]  # the task starts; the agent's first edit comes seconds later
    page.write_text(page.read_text().replace("<h1>Old</h1>", "<h1>Warmer</h1>"))
    await feed.capture(s, "t1", "site/index.html", "update")
    assert [r["added"] for r in events[-1]["regions"]] == [["<h1>Warmer</h1>"]] and events[-1]["removed"] == 1
    assert ("p", "node_modules/x.js") not in feed.seen
