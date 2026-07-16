import sys
import unittest
from pathlib import Path


CLEAN_DIR = Path(__file__).resolve().parents[1] / "scripts" / "02_clean"
sys.path.insert(0, str(CLEAN_DIR))

from build_policy_quality_six_topics import explicit_joint_departments  # noqa: E402


class JointDepartmentParserTests(unittest.TestCase):
    def test_preserves_geographic_parenthetical_qualifier(self) -> None:
        self.assertEqual(
            explicit_joint_departments(
                "中国(广东)自由贸易试验区工作办公室,广东省商务厅"
            ),
            ["中国(广东)自由贸易试验区工作办公室", "广东省商务厅"],
        )

    def test_does_not_split_aliases_inside_parentheses(self) -> None:
        self.assertEqual(
            explicit_joint_departments(
                "国家发展和改革委员会(含原国家发展计划委员会、原国家计划委员会)"
            ),
            [],
        )
        self.assertEqual(
            explicit_joint_departments(
                "中国人民银行营业管理部（中国人民银行北京营业管理部、"
                "中国人民银行营业管理部（北京））"
            ),
            [],
        )

    def test_accepts_explicit_top_level_issuer_list(self) -> None:
        self.assertEqual(
            explicit_joint_departments("科技部、教育部"),
            ["教育部", "科技部"],
        )


if __name__ == "__main__":
    unittest.main()
