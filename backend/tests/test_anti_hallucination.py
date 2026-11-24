"""Tests for anti-hallucination module (pattern + source layers, no LLM calls).

设计原则：检测器刻意保持保守，只标记高置信度的幻觉特征，
避免把正常的教学内容（如历史事实、合理统计）误判为幻觉。
"""

from datetime import datetime

import pytest
from app.services.anti_hallucination import PatternDetector, SourceValidator, ValidationResult


class TestPatternDetector:
    def setup_method(self):
        self.detector = PatternDetector()

    def test_clean_content_passes(self):
        result = self.detector.detect("这是正常的教学内容，不包含可疑引用。")
        assert result.passed is True
        assert result.issues == []

    def test_future_year_reference(self):
        future_year = datetime.now().year + 5
        result = self.detector.detect(f"据 {future_year} 年的最新研究显示，该方法有效。")
        assert result.passed is False
        assert any("未来年份" in i for i in result.issues)

    def test_future_year_published(self):
        future_year = datetime.now().year + 3
        result = self.detector.detect(f"根据 {future_year} 年发表在 Nature 上的论文")
        assert result.passed is False

    def test_current_year_is_allowed(self):
        """当前年与次年的引用是合理范围（预印本/近期成果），不应误报"""
        current_year = datetime.now().year
        result = self.detector.detect(f"据 {current_year} 年的最新研究显示，该方法有效。")
        assert result.passed is True

    def test_past_year_is_allowed(self):
        result = self.detector.detect("根据 2019 年发表在 Nature 上的论文")
        assert result.passed is True

    def test_exaggerated_accuracy(self):
        result = self.detector.detect("该模型的准确率达到了 99%")
        assert result.passed is False
        assert any("夸大" in i for i in result.issues)

    def test_exaggerated_improvement(self):
        result = self.detector.detect("使用本方法后，效率超过 99%")
        assert result.passed is False

    def test_moderate_improvement_passes(self):
        """80% 提升是合理量级，检测器刻意不标记以避免误伤"""
        result = self.detector.detect("使用本方法后，效率提升了 80%")
        assert result.passed is True

    def test_fake_expert_quote(self):
        """保守设计：不含未来年份的具名引用不判定为幻觉"""
        result = self.detector.detect("Hinton 在 2024 年提出了一种新方法")
        assert result.passed is True

    def test_absolute_statement(self):
        result = self.detector.detect("这种方法任何情况下都能 100% 正确")
        assert result.passed is False

    def test_fabricated_study(self):
        """保守设计：普通百分比调研不判定为幻觉（缺乏可验证依据）"""
        result = self.detector.detect("研究表明 75% 的学生更喜欢这种方法")
        assert result.passed is True

    def test_multiple_issues(self):
        future_year = datetime.now().year + 4
        result = self.detector.detect(
            f"据 {future_year} 年研究，该方法的准确率达到了 99.5%，并且 100% 正确。"
        )
        assert len(result.issues) >= 2
        assert result.confidence < 1.0

    def test_normal_scientific_content(self):
        result = self.detector.detect(
            "反向传播算法通过链式法则计算梯度，是深度学习训练的核心算法。"
        )
        assert result.passed is True

    def test_evaluation_related_content(self):
        result = self.detector.detect(
            "该学生的知识掌握度为 75 分，学习风格偏向视觉型。"
        )
        assert result.passed is True


class TestSourceValidator:
    def setup_method(self):
        self.validator = SourceValidator()

    def test_no_citation_no_source_passes(self):
        result = self.validator.validate_citations("正常内容，没有引用。")
        assert result.passed is True

    def test_citation_without_context(self):
        """无来源时，3 个以上引用才判定为可疑（少量引用视为常识性引用）"""
        result = self.validator.validate_citations("根据 [1][2][3] 可知...")
        assert result.passed is False
        assert any("引用" in i for i in result.issues)

    def test_single_citation_without_context_passes(self):
        result = self.validator.validate_citations("根据 [1] 可知...")
        assert result.passed is True

    def test_citation_with_context(self):
        chunks = [{"id": "1", "content": "参考内容"}]
        result = self.validator.validate_citations("根据 [1] 可知...", chunks)
        assert result.passed is True

    def test_mismatched_citation(self):
        chunks = [{"id": "2", "content": "参考内容"}]
        result = self.validator.validate_citations("根据 [2] 可知...", chunks)
        assert result.passed is False

    def test_chinese_reference_marker(self):
        """仅有"参考："字样且无编号引用时，不判定为问题"""
        result = self.validator.validate_citations("参考：一些引用内容")
        assert result.passed is True

    def test_no_citation_with_context(self):
        chunks = [{"id": "1", "content": "参考内容"}]
        result = self.validator.validate_citations("没有引用标记的内容", chunks)
        assert result.passed is True


class TestValidationResult:
    def test_default_values(self):
        r = ValidationResult(passed=True)
        assert r.issues == []
        assert r.confidence == 1.0
        assert r.raw_content == ""

    def test_custom_values(self):
        r = ValidationResult(passed=False, issues=["错误1"], confidence=0.3, raw_content="内容")
        assert r.passed is False
        assert r.issues == ["错误1"]
        assert r.confidence == 0.3
        assert r.raw_content == "内容"