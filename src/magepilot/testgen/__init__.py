"""PHPUnit test generation (docs/architecture/08, Phase 5) — graph-powered skeletons
(constructor mocks come from the knowledge graph, not guesses), optional coder-role
body fill, approval-gated writes, `vendor/bin/phpunit` as an ASK-tier command."""
from magepilot.testgen import mftf, playwright                                   # noqa: F401
from magepilot.testgen.phpunit import generate, skeleton, test_path, write_test  # noqa: F401
