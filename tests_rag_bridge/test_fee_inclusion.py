import unittest
from unittest.mock import patch

import main as customer_service
from rag_delivery import RAG_CONCISE_STYLE


class FeeInclusionReplyTests(unittest.TestCase):
    def test_domestic_city_with_administrative_suffix_is_classified(self):
        self.assertEqual(
            customer_service._classify_departure_location("上海市"),
            "domestic",
        )

    def test_known_overseas_gathering_point_is_classified(self):
        self.assertEqual(
            customer_service._classify_departure_location("巴厘岛"),
            "overseas",
        )

    def test_missing_or_unknown_gathering_point_is_not_guessed(self):
        for city in ("", "未知集合地"):
            with self.subTest(city=city):
                self.assertEqual(
                    customer_service._classify_departure_location(city),
                    "unknown",
                )

    def test_domestic_departure_includes_airfare(self):
        self.assertEqual(
            customer_service._fee_inclusion_reply("价格全包吗", "上海市"),
            "亲亲，这个方案含机票，行程未包含的其他费用需要自理。",
        )

    def test_overseas_meeting_point_requires_self_purchased_airfare(self):
        self.assertEqual(
            customer_service._fee_inclusion_reply("价格全包吗", "巴厘岛"),
            "亲亲，这个行程在巴厘岛集合，往返机票需要您自理，其他未包含的费用也需要自理。",
        )

    def test_unknown_meeting_point_does_not_claim_airfare_status(self):
        for city in ("", "未知集合地"):
            with self.subTest(city=city):
                self.assertEqual(
                    customer_service._fee_inclusion_reply("机票包不包", city),
                    "亲亲，这个费用不是全包的，机票是否包含要以集合地和行程说明为准，未包含的其他费用需要自理。",
                )

    def test_inclusion_statements_are_not_misclassified_as_customer_questions(self):
        self.assertIsNone(
            customer_service._fee_inclusion_reply("我们家有全包的产品", "上海市")
        )
        self.assertIsNone(
            customer_service._fee_inclusion_reply("这条线路已包含机票", "上海市")
        )

    def test_additional_explicit_inclusion_questions_are_recognized(self):
        self.assertEqual(
            customer_service._fee_inclusion_reply("费用都包含了吗", "上海市"),
            "亲亲，这个方案含机票，行程未包含的其他费用需要自理。",
        )
        self.assertEqual(
            customer_service._fee_inclusion_reply("行程含机票吗", "上海市"),
            "亲亲，这个方案含机票，行程未包含的其他费用需要自理。",
        )

    def test_price_question_is_not_misclassified_as_inclusion_question(self):
        self.assertIsNone(
            customer_service._fee_inclusion_reply("机票多少钱", "上海市")
        )
        self.assertIsNone(
            customer_service._fee_inclusion_reply("机票价格是多少", "上海市")
        )

    def test_deterministic_reply_survives_model_failure_without_contact_invitation(self):
        session_id = "fee-inclusion-regression-customer"
        customer_service.SESSIONS.pop("cust_" + session_id, None)
        with patch.object(
            customer_service,
            "_get_product_info",
            return_value={"destinations": ["巴厘岛"], "duration": "6日5晚"},
        ), patch.object(
            customer_service.im_core,
            "push_and_log",
            return_value={},
        ), patch.object(
            customer_service,
            "kb_chat",
            side_effect=RuntimeError("synthetic model outage"),
        ) as model_call:
            style_token = RAG_CONCISE_STYLE.set(True)
            try:
                result = customer_service.handle_customer_message(
                    session_id,
                    "70793312",
                    "巴厘岛+科莫多6日5晚私家团",
                    "价格全包吗",
                    history=[],
                    product_context={"departure_city": "上海市"},
                )
            finally:
                RAG_CONCISE_STYLE.reset(style_token)
                customer_service.SESSIONS.pop("cust_" + session_id, None)

        self.assertEqual(
            result["reply"],
            "亲亲，这个方案含机票，行程未包含的其他费用需要自理。",
        )
        self.assertEqual(result["analysis"]["intent"], "fee_inclusion")
        self.assertNotIn("联系方式", result["reply"])
        model_call.assert_not_called()

    def test_missing_current_context_does_not_reuse_cached_departure_city(self):
        session_id = "fee-inclusion-stale-context-customer"
        customer_service.SESSIONS.pop("cust_" + session_id, None)
        with patch.object(
            customer_service,
            "_get_product_info",
            return_value={
                "destinations": ["巴厘岛"],
                "duration": "6日5晚",
                "departure_city": "上海市",
            },
        ), patch.object(
            customer_service.im_core,
            "push_and_log",
            return_value={},
        ), patch.object(
            customer_service,
            "kb_chat",
            side_effect=RuntimeError("synthetic model outage"),
        ) as model_call:
            style_token = RAG_CONCISE_STYLE.set(True)
            try:
                result = customer_service.handle_customer_message(
                    session_id,
                    "70793312",
                    "巴厘岛+科莫多6日5晚私家团",
                    "价格全包吗",
                    history=[],
                    product_context={},
                )
            finally:
                RAG_CONCISE_STYLE.reset(style_token)
                customer_service.SESSIONS.pop("cust_" + session_id, None)

        self.assertEqual(
            result["reply"],
            "亲亲，这个费用不是全包的，机票是否包含要以集合地和行程说明为准，未包含的其他费用需要自理。",
        )
        self.assertEqual(result["analysis"]["intent"], "fee_inclusion")
        model_call.assert_not_called()

    def test_fee_inclusion_does_not_add_contact_invitation_in_legacy_mode(self):
        answer = "亲亲，这个方案含机票，行程未包含的其他费用需要自理。"
        style_token = RAG_CONCISE_STYLE.set(False)
        try:
            body, closing = customer_service._customer_answer_parts(
                answer, False, intent="fee_inclusion"
            )
        finally:
            RAG_CONCISE_STYLE.reset(style_token)

        self.assertEqual(body, answer)
        self.assertEqual(closing, "")


if __name__ == "__main__":
    unittest.main()
