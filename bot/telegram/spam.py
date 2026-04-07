import re


class SpamFilter:
    SPAM_PATTERNS = [
        re.compile(r"(?i)(crypto|bitcoin|earn\s+\$|free\s+money|investment\s+opportunity)"),
        re.compile(r"(?i)(click\s+here|join\s+now|limited\s+offer|act\s+fast)"),
        re.compile(r"(?i)(t\.me/\S+bot|@\S+bot)\s+(earn|free|money)"),
        re.compile(r"(?i)(onlyfans|adult|18\+|xxx)"),
        re.compile(r"(?i)(hamkorlik|reklama|pul\s+ishlash|daromad)\s.*(link|havola|kanal)"),
    ]

    SAFE_PATTERNS = [
        re.compile(r"(?i)(salom|assalomu|rahmat|yaxshi|qanday)"),
        re.compile(r"(?i)(savol|yordam|maslahat|fikr)"),
    ]

    def check(self, text: str) -> bool | None:
        """True=spam, False=safe, None=noaniq (AI kerak)"""
        for p in self.SAFE_PATTERNS:
            if p.search(text):
                return False
        for p in self.SPAM_PATTERNS:
            if p.search(text):
                return True
        return None
