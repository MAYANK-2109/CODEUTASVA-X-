import threading
import time

from fastapi.testclient import TestClient

from app.main import app
from app.tools import memory, sentiment


def test_health_reports_memory():
    body = TestClient(app).get("/health").json()
    assert body["status"] == "ok"
    assert set(body["memory"]) == {"used_mb", "limit_mb"}


def test_limit_comes_from_the_environment(monkeypatch):
    monkeypatch.setenv("MEMORY_LIMIT_MB", "512")
    assert memory.limit_mb() == 512


def test_room_is_judged_against_the_tight_share(monkeypatch):
    monkeypatch.setattr(memory, "limit_mb", lambda: 500.0)
    monkeypatch.setattr(memory, "used_mb", lambda: 300.0)
    assert memory.room_for(100)        # 400 of 500 is at the mark, not over it
    assert not memory.room_for(101)


def test_unknown_usage_never_blocks(monkeypatch):
    monkeypatch.setattr(memory, "limit_mb", lambda: 500.0)
    monkeypatch.setattr(memory, "used_mb", lambda: None)
    assert memory.room_for(10_000)


def test_heavy_work_runs_one_at_a_time():
    running, most = [0], [0]

    def work():
        with memory.heavy():
            running[0] += 1
            most[0] = max(most[0], running[0])
            time.sleep(0.05)
            running[0] -= 1

    threads = [threading.Thread(target=work) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert most[0] == 1


def test_heavy_releases_after_an_error():
    try:
        with memory.heavy():
            raise ValueError("boom")
    except ValueError:
        pass
    with memory.heavy():   # would wait out the timeout if the gate were still held
        pass


def test_relieve_sheds_the_model_only_when_tight(monkeypatch):
    shed = []
    monkeypatch.setattr(sentiment, "shed", lambda: shed.append(True))
    monkeypatch.setattr(memory, "limit_mb", lambda: 500.0)
    monkeypatch.setattr(memory, "used_mb", lambda: 300.0)
    memory.relieve()
    assert shed == []
    monkeypatch.setattr(memory, "used_mb", lambda: 450.0)
    memory.relieve()
    assert shed == [True]


def test_shed_model_scores_with_the_lexicon_and_says_so(monkeypatch):
    monkeypatch.delenv("SENTIMENT_MODEL", raising=False)
    monkeypatch.setattr(sentiment, "_finbert", {"session": object(), "tokenizer": object(), "tried": True,
                                                "error": None, "shed_until": 0.0})
    monkeypatch.setattr(sentiment, "_scores", {"old headline": 0.9})
    assert sentiment.backend() == sentiment.FINBERT
    sentiment.shed()
    assert sentiment.backend() == sentiment.VADER
    assert "VADER" in sentiment.source_label()
    assert sentiment._scores == {}                       # no model score is reused as a lexicon score
    assert sentiment.status()["error"] == "unloaded to stay inside the memory limit"
    assert -1 <= sentiment.score("Shares plunge after fraud probe") < 0


def test_model_is_not_loaded_without_room(monkeypatch, tmp_path):
    model, tokenizer = tmp_path / "model.onnx", tmp_path / "tokenizer.json"
    model.write_bytes(b"0" * 10)
    tokenizer.write_text("{}")
    monkeypatch.delenv("SENTIMENT_MODEL", raising=False)
    monkeypatch.setattr(sentiment, "MODEL_FILE", model)
    monkeypatch.setattr(sentiment, "TOKENIZER_FILE", tokenizer)
    monkeypatch.setattr(sentiment, "MODEL_MIN_BYTES", 1)
    monkeypatch.setattr(sentiment, "_finbert", {"session": None, "tokenizer": None, "tried": False,
                                                "error": None, "shed_until": 0.0})
    monkeypatch.setattr(memory, "room_for", lambda megabytes: False)
    assert sentiment.backend() == sentiment.VADER
    assert sentiment._finbert["error"] == "not enough free memory to load the model"
    assert sentiment._finbert["tried"] is False          # it is tried again once the pause is over


def test_render_scores_with_the_lexicon_unless_told_otherwise(monkeypatch):
    loads = []
    monkeypatch.setattr(sentiment, "_load_finbert", lambda: loads.append(True) or True)
    monkeypatch.delenv("SENTIMENT_MODEL", raising=False)
    monkeypatch.setenv("RENDER", "true")
    assert sentiment.backend() == sentiment.VADER and sentiment.status()["lexicon_only"]
    assert loads == []                                   # the model is never loaded, so its memory is never taken
    monkeypatch.setenv("SENTIMENT_MODEL", "finbert")     # an explicit setting wins, for a larger instance
    assert sentiment.backend() == sentiment.FINBERT
    monkeypatch.delenv("SENTIMENT_MODEL")
    monkeypatch.delenv("RENDER")
    assert sentiment.backend() == sentiment.FINBERT      # off Render the model is still the default
