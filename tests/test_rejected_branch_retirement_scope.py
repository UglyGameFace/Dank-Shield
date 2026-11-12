"""Guard against broadening owner-rejected branch retirement to recovery or active refs."""
from pathlib import Path
import re
import unittest


WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/retire-merged-temporary-branches.yml"


class RejectedBranchRetirementScopeTests(unittest.TestCase):
    def test_only_explicitly_rejected_heads_are_eligible(self):
        source = WORKFLOW.read_text(encoding="utf-8")
        start = source.index("const rejectedHeads = reconciled ? [")
        rejected_start = source.index("] : [", start) + len("] : [")
        rejected_end = source.index("\n              ];", rejected_start)
        rejected = source[rejected_start:rejected_end]
        heads = re.findall(r"\{name:'([^']+)'", rejected)
        self.assertEqual(
            heads,
            [
                "feat/cinema-rolling-supervised-worker-20261014",
                "fix/517-permanent-optout-20261013",
            ],
        )
        self.assertFalse(any(name.startswith(("backup/", "archive/")) for name in heads))
        self.assertIn("current.commit.sha !== target.sha", source)
        self.assertIn("current.protected", source)
        self.assertIn("openPRs.some", source)
        self.assertIn("archive = await readCreatedArchive", source)


if __name__ == "__main__":
    unittest.main()
