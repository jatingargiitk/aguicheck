"""Record what the official Python SDK, ag-ui-protocol 0.1.22, puts on the wire
for a run that streams its reasoning. 0.1.x still has the THINKING_* events
that 1.0 retired.

    pip install ag-ui-protocol==0.1.22
    python examples/record_sdk_thinking.py > examples/sdk-0.1.22-thinking.sse
"""

import sys

from ag_ui.core import (RunFinishedEvent, RunStartedEvent, TextMessageContentEvent, TextMessageEndEvent,
                        TextMessageStartEvent, ThinkingEndEvent, ThinkingStartEvent,
                        ThinkingTextMessageContentEvent, ThinkingTextMessageEndEvent,
                        ThinkingTextMessageStartEvent)
from ag_ui.encoder import EventEncoder

encoder = EventEncoder()
events = [
    RunStartedEvent(thread_id="t1", run_id="r1"),
    ThinkingStartEvent(title="Planning"),
    ThinkingTextMessageStartEvent(),
    ThinkingTextMessageContentEvent(delta="The user wants a short answer."),
    ThinkingTextMessageEndEvent(),
    ThinkingEndEvent(),
    TextMessageStartEvent(message_id="m1", role="assistant"),
    TextMessageContentEvent(message_id="m1", delta="Here it is."),
    TextMessageEndEvent(message_id="m1"),
    RunFinishedEvent(thread_id="t1", run_id="r1"),
]
for event in events:
    sys.stdout.write(encoder.encode(event))
