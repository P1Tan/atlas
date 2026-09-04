"""Milestone 9.1 (NFR2, reliability): decides whether a pipeline-level
`ErrorFrame` (caught via `worker.event_handler("on_pipeline_error")` in
`app/voice_agent.py`) warrants telling iOS, and what to send if so.

Split out from `voice_agent.py`'s inline event handler so this decision is
unit-testable on its own -- unlike the transcript/reply/tool-result bridges
(`app/voice_transcript_bridge.py` etc.), this isn't a Pipecat `FrameProcessor`
(there's no such thing as a worker-level event handler processor; it's a
plain callback registered on the `PipelineWorker` itself), so it can't use
`pipecat.tests.utils.run_test`'s FrameProcessor harness. A plain function
taking an `ErrorFrame` and returning the message payload (or `None`) needs no
harness at all.

In THIS pipeline's current usage, `ErrorCategory.APPLICATION` means a tool
handler raised. Pipecat's own function-call runner (`services/llm_service.py`,
confirmed via the installed source) already catches that, feeds a synthetic
error result back into the conversation on the handler's behalf, and the
model explains the failure in its own reply -- `LiveKitAssistantReplyBridge`
already forwards that as a normal `assistant_reply`, same as any other reply.
Notifying here too would be redundant, unexplained noise stacked on top of a
failure the user is already about to hear about gracefully.

Caution for future changes: APPLICATION is not *exclusively* the recovered
tool-call case at the Pipecat framework level -- a registered TTS text
transform that raises (`services/tts_service.py`) also reports
`category=ErrorCategory.APPLICATION`, but that path is NOT auto-recovered
(no synthetic result, the turn just produces no audio). This pipeline
(`app/voice_agent.py`) doesn't register any TTS text transform today, so
that path is dormant, not live -- but if one is ever added, this skip
condition would silently swallow its failures too, reopening the exact
silent-hang gap this module exists to close. Revisit this condition (e.g.
also check which processor the frame came from) if that ever changes.

Every other category (rate limit, quota, connectivity, server, an
unrecognized category, etc.) means the LLM or TTS provider call itself
failed -- no reply is coming for this turn, and nothing else in the pipeline
surfaces that. In practice `frame.category` is never actually `None` by the
time this runs: `FrameProcessor.push_error_frame` (confirmed via the
installed source) backfills an unset category from the exception (or
`ErrorCategory.UNKNOWN` as a last resort) before the frame ever travels --
handled here anyway, defensively, since `None` still correctly falls into
"notify" rather than being silently swallowed.
"""

from typing import Optional

from pipecat.frames.frames import ErrorFrame
from pipecat.utils.errors import ErrorCategory

# A fixed, generic message for iOS -- never the raw exception text, which
# could contain internal provider/error detail (model names, stack traces).
# The real detail is logged server-side only, by voice_agent.py's caller.
PIPELINE_ERROR_MESSAGE_FOR_IOS = "Sorry, something went wrong on my end. Please try again."


def pipeline_error_notification(frame: ErrorFrame) -> Optional[dict]:
    """The LiveKit data-message payload to send to iOS for this pipeline
    error, or `None` if it should be ignored (an already-gracefully-handled
    tool failure)."""
    if frame.category == ErrorCategory.APPLICATION:
        return None
    return {"type": "pipeline_error", "message": PIPELINE_ERROR_MESSAGE_FOR_IOS}
