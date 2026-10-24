import tempfile
import threading
import unittest
from pathlib import Path

from rag_delivery import DeliveryClaim, DeliveryRecord, DeliveryScope, DeliveryStore


class MutableClock:
    def __init__(self, value=1_800_000_000.0):
        self.value = value

    def __call__(self):
        return self.value


class DeliveryStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db_path = Path(self.temp_dir.name) / "delivery.sqlite3"
        self.clock = MutableClock()
        self.store = DeliveryStore(self.db_path, clock=self.clock)

    def scope(self, customer_id="customer-1", conversation_id="conversation-1"):
        return DeliveryScope(
            tenant_id="indonesia_brother",
            channel="c_trip",
            customer_id=customer_id,
            conversation_id=conversation_id,
        )

    def test_pending_retry_reuses_the_exact_generated_reply(self):
        scope = self.scope()
        claim = self.store.claim(scope, "message-1")
        self.assertIsInstance(claim, DeliveryClaim)

        self.store.complete(
            claim,
            reply="We can help arrange the airport transfer.",
            action="reply",
            trace_id="trace-1",
            send_reply=True,
        )

        retry = self.store.claim(scope, "message-1")
        self.assertIsInstance(retry, DeliveryRecord)
        self.assertEqual(retry.state, "pending")
        self.assertEqual(retry.reply, "We can help arrange the airport transfer.")
        self.assertEqual(retry.action, "reply")
        self.assertEqual(retry.trace_id, "trace-1")

    def test_acknowledgement_is_idempotent_and_suppresses_later_claims(self):
        scope = self.scope()
        claim = self.store.claim(scope, "message-2")
        self.store.complete(
            claim,
            reply="Your booking includes breakfast.",
            action="reply",
            trace_id="trace-2",
            send_reply=True,
        )

        self.assertTrue(self.store.acknowledge(scope, "message-2"))
        self.assertTrue(self.store.acknowledge(scope, "message-2"))
        after_ack = self.store.claim(scope, "message-2")
        self.assertIsInstance(after_ack, DeliveryRecord)
        self.assertEqual(after_ack.state, "acknowledged")

    def test_send_reply_false_is_persisted_as_acknowledged(self):
        scope = self.scope()
        claim = self.store.claim(scope, "notification-1")
        self.store.complete(
            claim,
            reply="",
            action="ignore",
            trace_id="trace-3",
            send_reply=False,
        )

        result = self.store.claim(scope, "notification-1")
        self.assertIsInstance(result, DeliveryRecord)
        self.assertEqual(result.state, "acknowledged")
        self.assertEqual(result.action, "ignore")
        self.assertEqual(result.reply, "")

    def test_distinct_customers_and_conversations_have_independent_claims(self):
        first = self.store.claim(self.scope(customer_id="customer-a"), "same-id")
        second = self.store.claim(self.scope(customer_id="customer-b"), "same-id")
        third = self.store.claim(
            self.scope(customer_id="customer-a", conversation_id="conversation-2"),
            "same-id",
        )

        self.assertIsInstance(first, DeliveryClaim)
        self.assertIsInstance(second, DeliveryClaim)
        self.assertIsInstance(third, DeliveryClaim)
        self.assertNotEqual(first.lease_token, second.lease_token)

    def test_only_one_connection_claims_a_message_concurrently(self):
        scope = self.scope()
        barrier = threading.Barrier(2)
        results = []
        failures = []

        def claim_in_separate_connection():
            try:
                store = DeliveryStore(self.db_path, clock=self.clock)
                barrier.wait(timeout=5)
                results.append(store.claim(scope, "concurrent-1"))
            except Exception as exc:
                failures.append(exc)

        workers = [
            threading.Thread(target=claim_in_separate_connection),
            threading.Thread(target=claim_in_separate_connection),
        ]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=10)

        self.assertFalse(failures, failures)
        self.assertTrue(all(not worker.is_alive() for worker in workers))
        self.assertEqual(sum(isinstance(item, DeliveryClaim) for item in results), 1)
        self.assertEqual(sum(
            isinstance(item, DeliveryRecord) and item.state == "processing"
            for item in results
        ), 1)

    def test_expired_processing_lease_can_be_reclaimed_and_stale_owner_cannot_complete(self):
        scope = self.scope()
        old_claim = self.store.claim(scope, "lease-1")
        self.clock.value += 601

        new_claim = self.store.claim(scope, "lease-1")
        self.assertIsInstance(new_claim, DeliveryClaim)
        self.assertNotEqual(old_claim.lease_token, new_claim.lease_token)

        with self.assertRaises(RuntimeError):
            self.store.complete(
                old_claim,
                reply="stale reply",
                action="reply",
                trace_id="stale",
                send_reply=True,
            )

        current = self.store.claim(scope, "lease-1")
        self.assertIsInstance(current, DeliveryRecord)
        self.assertEqual(current.state, "processing")

    def test_release_after_generation_failure_allows_a_fresh_claim(self):
        scope = self.scope()
        claim = self.store.claim(scope, "failed-generation")
        try:
            raise ValueError("synthetic generation failure")
        except ValueError:
            self.store.release(claim)

        retry = self.store.claim(scope, "failed-generation")
        self.assertIsInstance(retry, DeliveryClaim)
        self.assertNotEqual(claim.lease_token, retry.lease_token)

    def test_pending_and_acknowledged_records_expire_after_retention(self):
        scope = self.scope()
        claim = self.store.claim(scope, "retention-1")
        self.store.complete(
            claim,
            reply="temporary reply",
            action="reply",
            trace_id="trace-4",
            send_reply=True,
        )
        self.clock.value += 24 * 60 * 60 + 1

        new_claim = self.store.claim(scope, "retention-1")
        self.assertIsInstance(new_claim, DeliveryClaim)

    def test_acknowledging_an_unknown_message_returns_false(self):
        self.assertFalse(self.store.acknowledge(self.scope(), "missing"))

    def test_invalid_scope_ids_are_rejected_before_claiming(self):
        with self.assertRaises(ValueError):
            self.store.claim(self.scope(customer_id=""), "message-1")
        with self.assertRaises(ValueError):
            self.store.claim(self.scope(), "")


if __name__ == "__main__":
    unittest.main()