from pipecat.frames.frames import ErrorFrame
from pipecat.utils.errors import ErrorCategory

from app.voice_pipeline_error_bridge import PIPELINE_ERROR_MESSAGE_FOR_IOS, pipeline_error_notification


def test_application_category_is_ignored() -> None:
    # A tool handler failure -- Pipecat's own function-call runner already
    # recovered gracefully and the model will explain it in a normal reply.
    frame = ErrorFrame(error="tool failed", category=ErrorCategory.APPLICATION)
    assert pipeline_error_notification(frame) is None


def test_provider_failure_categories_produce_a_notification() -> None:
    for category in (
        ErrorCategory.RATE_LIMIT,
        ErrorCategory.QUOTA,
        ErrorCategory.CONNECTIVITY,
        ErrorCategory.SERVER,
        ErrorCategory.AUTHENTICATION,
        ErrorCategory.UNKNOWN,
    ):
        frame = ErrorFrame(error="the provider call failed", category=category)
        notification = pipeline_error_notification(frame)
        assert notification == {"type": "pipeline_error", "message": PIPELINE_ERROR_MESSAGE_FOR_IOS}


def test_unset_category_still_produces_a_notification() -> None:
    # Defensive, not the realistic shape: `FrameProcessor.push_error_frame`
    # backfills an unset category (from the exception, or ErrorCategory.UNKNOWN
    # as a last resort) before a real ErrorFrame ever travels, so `None`
    # shouldn't actually reach here in production -- but if it somehow did,
    # it must still notify rather than being silently swallowed like
    # APPLICATION is.
    frame = ErrorFrame(error="completion failed")
    assert frame.category is None
    notification = pipeline_error_notification(frame)
    assert notification == {"type": "pipeline_error", "message": PIPELINE_ERROR_MESSAGE_FOR_IOS}


def test_notification_never_leaks_the_raw_error_text() -> None:
    frame = ErrorFrame(error="sk-secret-looking-detail-from-a-stack-trace", category=ErrorCategory.SERVER)
    notification = pipeline_error_notification(frame)
    assert "sk-secret-looking-detail-from-a-stack-trace" not in notification["message"]
