import unittest
from types import SimpleNamespace
from unittest.mock import patch

import main as customer_service
from rag_delivery import RAG_CONCISE_STYLE


class RagConciseStyleTests(unittest.TestCase):
    def test_rag_ordinary_answers_skip_contact_closing(self):
        token = RAG_CONCISE_STYLE.set(True)
        try:
            body, closing = customer_service._customer_answer_parts(
                "巴厘岛有适合孩子的活动。", False, intent="valid"
            )
        finally:
            RAG_CONCISE_STYLE.reset(token)

        self.assertEqual(body, "巴厘岛有适合孩子的活动。")
        self.assertEqual(closing, "")

    def test_rag_explicit_contact_handoff_keeps_contact_closing(self):
        token = RAG_CONCISE_STYLE.set(True)
        try:
            _, closing = customer_service._customer_answer_parts(
                "可以的。", False, intent="contact_handoff"
            )
        finally:
            RAG_CONCISE_STYLE.reset(token)

        self.assertTrue(closing)

    def test_legacy_answers_keep_existing_contact_closing(self):
        _, closing = customer_service._customer_answer_parts(
            "巴厘岛有适合孩子的活动。", False, intent="valid"
        )

        self.assertTrue(closing)

    def test_rag_length_limit_is_scoped_to_rag_context(self):
        long_answer = "答" * 200
        token = RAG_CONCISE_STYLE.set(True)
        try:
            rag_answer = customer_service._finalize_answer(long_answer)
        finally:
            RAG_CONCISE_STYLE.reset(token)

        legacy_answer = customer_service._finalize_answer(long_answer)
        self.assertEqual(len(rag_answer), 80)
        self.assertEqual(len(legacy_answer), customer_service.MAX_ANSWER_LEN)

    def test_rag_summarizes_single_faq_but_legacy_keeps_authored_answer(self):
        faq = [{"question": "带小孩去哪玩", "answer": "带小孩游玩可以安排亲子体验。"}]
        completion = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="带孩子可以安排亲子活动。"))]
        )
        with patch.object(customer_service.client.chat.completions, "create", return_value=completion) as model_call:
            token = RAG_CONCISE_STYLE.set(True)
            try:
                rag_result = customer_service.generate_single_answer("带小孩怎么玩", faq, [])
            finally:
                RAG_CONCISE_STYLE.reset(token)

        self.assertEqual(rag_result["source"], "faq")
        self.assertEqual(rag_result["answer"], "带孩子可以安排亲子活动。")
        system_prompt = model_call.call_args.kwargs["messages"][0]["content"]
        self.assertIn("不要每条都固定开头", system_prompt)
        self.assertIn("普通咨询只回答当前问题", system_prompt)

        with patch.object(customer_service.client.chat.completions, "create") as legacy_call:
            legacy_result = customer_service.generate_single_answer("带小孩怎么玩", faq, [])

        self.assertEqual(legacy_result["answer"], faq[0]["answer"])
        legacy_call.assert_not_called()


if __name__ == "__main__":
    unittest.main()
