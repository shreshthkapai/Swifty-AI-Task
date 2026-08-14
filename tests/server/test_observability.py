import json
import unittest
from datetime import UTC, datetime

from server.observability import (
    JsonEventLogger,
    ObservedConversationProvider,
    ObservedDealerAdapter,
    ObservedGroundedResponseProvider,
    ObservedPlanningProvider,
    conversation_hash,
    log_context,
)
from webchat.harness.conversation import (
    ConversationRequest,
    ConversationResult,
    ConversationUsage,
)
from webchat.harness.conversation_tools import conversation_tool_catalogue
from webchat.harness.contracts import (
    ReadCommandName,
    ResponseStrategy,
    TurnPlan,
    TurnScope,
)
from webchat.harness.tool_gate import command_spec
from webchat.harness.evidence import EvidenceEnvelope
from webchat.harness.grounded_response import (
    GroundedClaim,
    GroundedClaimKind,
    GroundedResponseRequest,
    GroundedResponseState,
)
from webchat.harness.state import ConversationState
from webchat.providers.base import (
    GroundedResponseResult,
    PlanningRequest,
    PlanningResult,
    ProviderUsage,
)


class JsonEventLoggerTests(unittest.TestCase):
    def test_emits_canonical_metadata_without_unknown_or_sensitive_values(self) -> None:
        lines: list[str] = []
        logger = JsonEventLogger(sink=lines.append)

        with log_context(
            request_id="request-1",
            turn_id="turn-1",
            conversation_id="opaque-conversation-token",
            route="POST /api/chat/turns",
        ):
            logger.emit(
                "chat_turn",
                operation="handle_turn",
                duration_ms=12.3456,
                retries=0,
                model_calls=1,
                input_tokens=120,
                output_tokens=20,
                outcome="ok",
                api_key="must-not-appear",
                email="jamie@example.com",
                message="private customer text",
            )

        self.assertEqual(len(lines), 1)
        event = json.loads(lines[0])
        self.assertEqual(event["event"], "chat_turn")
        self.assertEqual(event["request_id"], "request-1")
        self.assertEqual(event["conversation_hash"], conversation_hash("opaque-conversation-token"))
        self.assertEqual(event["duration_ms"], 12.346)
        self.assertNotIn("api_key", event)
        self.assertNotIn("email", event)
        self.assertNotIn("message", event)
        serialized = lines[0]
        self.assertNotIn("must-not-appear", serialized)
        self.assertNotIn("jamie@example.com", serialized)
        self.assertNotIn("private customer text", serialized)
        self.assertNotIn("opaque-conversation-token", serialized)

    def test_context_is_reset_after_request(self) -> None:
        lines: list[str] = []
        logger = JsonEventLogger(sink=lines.append)

        with log_context(request_id="request-1", conversation_id="conversation-1"):
            logger.emit("inside", outcome="ok")
        logger.emit("outside", outcome="ok")

        outside = json.loads(lines[1])
        self.assertNotIn("request_id", outside)
        self.assertNotIn("conversation_hash", outside)


class ExternalCallLoggingTests(unittest.IsolatedAsyncioTestCase):
    async def test_conversation_call_logs_metrics_without_context(self) -> None:
        lines: list[str] = []
        logger = JsonEventLogger(sink=lines.append)
        result = ConversationResult(
            text="A useful answer.",
            usage=ConversationUsage(20, 5, 25),
            latency_ms=3,
            time_to_first_token_ms=2,
            provider="fixture-provider",
            model="fixture-model",
        )

        class Provider:
            async def converse(self, request, *, on_text_delta=None):
                return result

        observed = ObservedConversationProvider(Provider(), logger=logger)
        request = ConversationRequest(
            context='{"current_input":"PRIVATE QUESTION"}',
            tools=conversation_tool_catalogue(),
        )

        returned = await observed.converse(request)

        self.assertIs(returned, result)
        event = json.loads(lines[0])
        self.assertEqual(event["operation"], "conversation")
        self.assertEqual(event["input_tokens"], 20)
        self.assertEqual(event["output_tokens"], 5)
        self.assertNotIn("PRIVATE QUESTION", lines[0])

    async def test_dealer_call_records_operation_outcome_and_actual_retry_count(self) -> None:
        lines: list[str] = []
        logger = JsonEventLogger(sink=lines.append)
        retry_state = {"count": 99}

        class Dealer:
            async def list_dealerships(self):
                retry_state["count"] = 2
                return ("location",)

        observed = ObservedDealerAdapter(
            Dealer(),
            logger=logger,
            retry_reset=lambda: retry_state.update(count=0),
            retry_count=lambda: retry_state["count"],
        )

        with log_context(
            request_id="request-1",
            turn_id="turn-1",
            conversation_id="conversation-1",
            route="POST /api/chat/turns",
        ):
            result = await observed.list_dealerships()

        self.assertEqual(result, ("location",))
        event = json.loads(lines[0])
        self.assertEqual(event["event"], "external_call")
        self.assertEqual(event["operation"], "list_dealerships")
        self.assertEqual(event["retries"], 2)
        self.assertEqual(event["outcome"], "ok")

    async def test_provider_call_records_usage_without_context_payload(self) -> None:
        lines: list[str] = []
        logger = JsonEventLogger(sink=lines.append)
        result = PlanningResult(
            plan=TurnPlan(
                TurnScope.DEALERSHIP_ADJACENT,
                (),
                ResponseStrategy.GENERAL_GUIDANCE,
            ),
            usage=ProviderUsage(30, 8, 38),
            latency_ms=4,
            provider="fixture-provider",
            model="fixture-model",
        )

        class Provider:
            async def plan(self, request):
                return result

        observed = ObservedPlanningProvider(Provider(), logger=logger)
        request = PlanningRequest(
            context='{"private":"PRIVATE CONTEXT"}',
            commands=(command_spec(ReadCommandName.SEARCH_VEHICLES.value),),
        )
        returned = await observed.plan(request)

        self.assertIs(returned, result)
        event = json.loads(lines[0])
        self.assertEqual(event["event"], "external_call")
        self.assertEqual(event["operation"], "plan")
        self.assertEqual(event["provider"], "fixture-provider")
        self.assertEqual(event["model"], "fixture-model")
        self.assertEqual(event["input_tokens"], 30)
        self.assertNotIn("PRIVATE CONTEXT", lines[0])

    async def test_unexpected_external_failure_is_logged_without_exception_text(self) -> None:
        lines: list[str] = []
        logger = JsonEventLogger(sink=lines.append)

        class Dealer:
            async def get_business_information(self):
                raise RuntimeError("provider-secret customer@example.com")

        observed = ObservedDealerAdapter(Dealer(), logger=logger)
        with self.assertRaises(RuntimeError):
            await observed.get_business_information()

        event = json.loads(lines[0])
        self.assertEqual(event["outcome"], "error")
        self.assertEqual(event["error_kind"], "unexpected_error")
        self.assertNotIn("provider-secret", lines[0])
        self.assertNotIn("customer@example.com", lines[0])

    async def test_grounded_response_logs_usage_without_question_or_evidence(self) -> None:
        lines: list[str] = []
        logger = JsonEventLogger(sink=lines.append)
        result = GroundedResponseResult(
            claims=(GroundedClaim(
                "Compare the space you need.",
                GroundedClaimKind.GENERAL_GUIDANCE,
            ),),
            usage=ProviderUsage(25, 6, 31),
            latency_ms=3,
            provider="fixture-provider",
            model="fixture-model",
        )

        class Provider:
            async def respond(self, request):
                return result

        now = datetime(2026, 8, 14, 12, tzinfo=UTC)
        request = GroundedResponseRequest(
            question="PRIVATE QUESTION",
            state=GroundedResponseState.from_conversation(ConversationState()),
            evidence=EvidenceEnvelope((), now),
        )
        observed = ObservedGroundedResponseProvider(Provider(), logger=logger)

        returned = await observed.respond(request)

        self.assertIs(returned, result)
        event = json.loads(lines[0])
        self.assertEqual(event["operation"], "grounded_response")
        self.assertEqual(event["input_tokens"], 25)
        self.assertNotIn("PRIVATE QUESTION", lines[0])
