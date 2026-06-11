"""The deterministic Magento linter — MP001–MP015 (docs/architecture/07).

Pure regex, unit-testable without a model. BLOCK rules are architecture enforcement as
rails: the model's opinion is irrelevant, the linter refuses. WARN/INFO show alongside
the diff preview; the human decides.
"""
import fnmatch
import re
from dataclasses import dataclass

BLOCK, WARN, INFO = "block", "warn", "info"


@dataclass(frozen=True)
class Rule:
    id: str
    severity: str
    globs: tuple          # filename globs the rule applies to
    pattern: str          # regex over file content
    message: str
    suggestion: str = ""
    flags: int = re.M

    def regex(self):
        return re.compile(self.pattern, self.flags)


PHP = ("*.php", "*.phtml")

RULES = (
    Rule("MP001", BLOCK, PHP,
         r"ObjectManager\s*::\s*getInstance\s*\(",
         "ObjectManager::getInstance() — use constructor dependency injection",
         "inject the dependency via the constructor and declare it in di.xml if needed"),
    Rule("MP002", BLOCK, PHP,
         r"\$_(?:GET|POST|REQUEST|SESSION|COOKIE)\b",
         "PHP superglobal — use RequestInterface / SessionManager / CookieManager",
         "inject \\Magento\\Framework\\App\\RequestInterface and use getParam()"),
    Rule("MP003", BLOCK, PHP,
         r"\b(?:exec|shell_exec|system|passthru|popen|proc_open|eval)\s*\(",
         "dangerous function (exec/eval family) is forbidden in module code"),
    Rule("MP004", BLOCK, PHP,
         r"\bunserialize\s*\(",
         "unserialize() is unsafe — use SerializerInterface (Json)",
         "inject \\Magento\\Framework\\Serialize\\SerializerInterface"),
    Rule("MP005", BLOCK, ("*.php",),
         r"->(?:query|fetchAll|fetchRow|fetchOne|fetchCol|fetchPairs)\s*\(\s*[\"'][^\"']*[\"']\s*\.\s*\$",
         "SQL string concatenated with a variable — SQL injection",
         "use bound parameters or the repository/collection API"),
    Rule("MP006", BLOCK, ("*",),
         r"(?:(?:api[_-]?key|apikey|secret|password|token)\s*[=:]\s*['\"][A-Za-z0-9+/_\-]{12,}['\"]"
         r"|AKIA[0-9A-Z]{16}"
         r"|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY)",
         "hardcoded credential/secret detected",
         "read secrets from configuration (core_config_data encrypted backend) or env",
         re.M | re.I),
    # MP007 is a PATH rule — implemented in scan.scan_op, kept here for the catalog
    Rule("MP007", BLOCK, (),
         r"$^",  # never matches content; path policy enforces it
         "writes to vendor/, generated/, or app/etc/env.php are refused"),
    Rule("MP008", WARN, ("*di.xml",),
         r"<preference\s+for=",
         "preference replaces the whole implementation — prefer a plugin unless "
         "replacement is genuinely intended"),
    Rule("MP009", WARN, ("*.phtml",),
         r"(?:echo|<\?=)\s*\$(?!escaper|block->escape)(?![^;\n]*escape(?:Html|Url|Js|Css|HtmlAttr))[a-zA-Z_][^;\n]*;?",
         "unescaped output in a template (XSS)",
         "use $escaper->escapeHtml() / escapeUrl() / escapeJs() per context"),
    Rule("MP010", WARN, ("*.php",),
         r"(?:foreach|while)\s*\([^)]*\)[^}]{0,200}->load\s*\(",
         "model load inside a loop — N+1 query pattern",
         "use a collection or repository getList() with SearchCriteria"),
    Rule("MP011", WARN, ("*.php",),
         r"\b(?:CREATE|ALTER|DROP)\s+TABLE\b",
         "raw DDL in PHP — use declarative schema (db_schema.xml)"),
    Rule("MP012", WARN, ("*.phtml", "*.js", "*.html"),
         r"(?:innerHTML\s*=\s*(?![\"'`]\s*[\"'`])[^\"'`\n]|x-html=\"(?!')|v-html=)",
         "dynamic HTML injection (DOM XSS risk)",
         "prefer textContent / x-text, or sanitize known-safe HTML"),
    Rule("MP013", WARN, ("*.php",),
         r"\b(?:die|exit)\s*[(;]",
         "die()/exit() in module code — throw a typed exception instead",
         "throw \\Magento\\Framework\\Exception\\LocalizedException"),
    Rule("MP014", WARN, ("*.php",),
         r"\bcurl_(?:init|exec|setopt)\s*\(",
         "raw curl — use the framework HTTP client",
         "inject \\Magento\\Framework\\HTTP\\Client\\Curl or a PSR-18 client"),
    Rule("MP015", INFO, ("*.php",),
         r"\A(?![\s\S]{0,200}declare\s*\(\s*strict_types\s*=\s*1\s*\))",
         "missing declare(strict_types=1)",
         "add `declare(strict_types=1);` directly after `<?php`"),
)


@dataclass(frozen=True)
class Finding:
    rule_id: str
    severity: str
    path: str
    line: int
    message: str
    suggestion: str = ""

    def render(self) -> str:
        sev = {"block": "⛔", "warn": "⚠", "info": "ℹ"}[self.severity]
        out = f"{sev} {self.rule_id} {self.path}:{self.line} — {self.message}"
        if self.suggestion:
            out += f"\n      fix: {self.suggestion}"
        return out


def lint_content(path: str, content: str) -> list[Finding]:
    """All rule findings for one file's (proposed) content."""
    name = path.rsplit("/", 1)[-1]
    findings = []
    for rule in RULES:
        if not rule.globs or not any(fnmatch.fnmatch(name, g) or fnmatch.fnmatch(path, g)
                                     for g in rule.globs):
            continue
        m = rule.regex().search(content)
        if m:
            line = content.count("\n", 0, m.start()) + 1
            findings.append(Finding(rule.id, rule.severity, path, line,
                                    rule.message, rule.suggestion))
    return findings
