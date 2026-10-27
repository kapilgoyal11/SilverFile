from langgraph.types import Command

from src.intake_graph import build_intake_graph
from src.memory import get_checkpointer, thread_config

# A clean run: married filing jointly, one dependent, a few documents.
CLEAN_RUN = [
    "yes", "Margaret Ann Lee", "March 4, 1950", "no", "no", "yes", "yes",
    "1", "Robert Lee", "June 1 1948", "no",
    "yes", "Emma Lee", "granddaughter", "901-23-4567", "no",
    "yes", "1", "yes", "yes", "1", "no", "yes", "no", "no", "no",
    "yes", "$1,200", "no",
    "no",   # other Schedule 1, 2 or 3 amounts
    "no",   # other withholding (e.g. W-2G)
    "yes",  # confirm
]


class Session:
    """Drives the intake graph like a user would, recording every prompt."""

    def __init__(self, session_id="test", graph=None, db=":memory:"):
        self.graph = graph or build_intake_graph(get_checkpointer(db))
        self.cfg = thread_config(session_id)
        self.prompts: list[dict] = []
        self.result = self.graph.invoke({"session_id": session_id}, self.cfg)
        self._capture()

    def _capture(self):
        if "__interrupt__" in self.result:
            self.prompts.append(self.result["__interrupt__"][0].value)

    @property
    def last(self) -> dict:
        return self.prompts[-1]

    @property
    def waiting(self) -> bool:
        return "__interrupt__" in self.result

    def say(self, text):
        self.result = self.graph.invoke(Command(resume=text), self.cfg)
        self._capture()
        return self

    def say_all(self, replies):
        for r in replies:
            self.say(r)
        return self

    @property
    def state(self) -> dict:
        return self.graph.get_state(self.cfg).values
