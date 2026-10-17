import pytest

from firedev.risk import classify, unwrap


@pytest.mark.parametrize("cmd,level", [
    ("ls -la", 0), ("git status", 0), ("git diff HEAD", 0), ("cat app/build.gradle.kts", 0), ("sed -n 1,80p a.kt", 0),
    ("rg --files", 0), ("./gradlew testDebugUnitTest", 0), ("npm test", 0), ("pytest -q", 0), ("firedev run build", 0),
    ("cat scripts/deploy-preview.sh", 0), ("ls db/migrate", 0), ("tail -n 40 server.log", 0),
    ("sed -i 's/a/b/' x.kt", 1), ("echo hi > notes.txt", 1), ("git commit -m fix", 1), ("mv a b", 1), ("npm install", 1),
    ("rm build.log", 1), ("some-unknown-tool --flag", 1), ("apply_patch", 1),
    ("git push origin main", 2), ("sudo systemctl restart dashboard", 2), ("systemctl restart nginx", 2),
    ("apt-get install -y jq", 2), ("pip install requests", 2), ("./scripts/deploy-preview.sh", 2), ("rails db:migrate", 2),
    ("scp app.apk host:", 2), ("curl -X POST https://x", 2), ("docker compose up -d", 2), ("npm publish", 2),
    ("rm -rf /var/www", 3), ("sudo rm -rf /", 3), ("git push --force", 3), ("git reset --hard HEAD~3", 3),
    ("ufw allow 22", 3), ("sudo iptables -F", 3), ("chmod -R 777 /srv", 3), ("useradd bob", 3),
    ("psql -c 'DROP TABLE users'", 3), ("dd if=/dev/zero of=/dev/sda", 3), ("find . -name '*.log' -delete", 3), ("reboot", 3),
])
def test_levels(cmd, level):
    assert classify(cmd).level == level, classify(cmd)


def test_compound_takes_highest():
    assert classify("git status && git push").level == 2
    assert classify("ls; rm -rf build/").level == 3
    assert classify("cat a | grep b").level == 0


def test_unwraps_agent_shell_wrapper():
    assert unwrap("/bin/bash -lc 'git push origin main'") == "git push origin main"
    assert unwrap('/bin/bash -lc "sed -n 1,80p app/build.gradle.kts"') == "sed -n 1,80p app/build.gradle.kts"
    assert classify("/bin/bash -lc 'sudo systemctl restart x'").level == 2


def test_env_prefix_and_explanations():
    r = classify("FOO=1 git push")
    assert r.level == 2 and "remote" in r.explanation
    assert classify("sudo rm -rf /srv/app").label == "Recursive delete"
