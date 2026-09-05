"""The whole idea, in one small file. Run it:  python docs/learning/playground/tiny.py

Forget MedReport for ten minutes. Here is a made-up app: sign up a user and send
them a welcome message.
"""

from typing import Protocol

# ============================================================================
# THE BAD VERSION — everything in one function
# ============================================================================


def signup_bad(email: str) -> None:
    if "@" not in email:
        raise ValueError("bad email")

    # pretend this is a real Gmail API call
    print(f"   [gmail] connecting to smtp.gmail.com...")
    print(f"   [gmail] sent welcome to {email}")


# Ask yourself: how would you TEST this?
# You cannot. Running it sends a real email. Every single time.
# And if you switch from Gmail to WhatsApp, you edit this function --
# the one that also holds your signup rule.


# ============================================================================
# THE GOOD VERSION — the same thing, pulled apart
# ============================================================================

# --- 1. DOMAIN: the rule ----------------------------------------------------
# True regardless of Gmail, HTTP, databases, or the year. Just a rule.


def is_valid_email(email: str) -> bool:
    return "@" in email


# --- 2. PORT: what we NEED, without saying who provides it ------------------
# Read it as a sentence: "I need something that can send a message to someone."
# Notice: no Gmail, no SMTP, no API key. Just the need.


class Notifier(Protocol):
    def send(self, to: str, message: str) -> None: ...


# --- 3. USE CASE: the sequence of steps -------------------------------------
# One thing a user can do. It uses the RULE and the PORT.
# It does not know who will actually send the message.


def signup(email: str, notifier: Notifier) -> None:
    if not is_valid_email(email):
        raise ValueError("bad email")
    notifier.send(email, "Welcome to MedReport!")


# --- 4. ADAPTERS: things that satisfy the port ------------------------------
# Each one is a different HOW. All of them fit the same socket.


class GmailNotifier:
    def send(self, to: str, message: str) -> None:
        print(f"   [gmail]    connecting to smtp.gmail.com...")
        print(f"   [gmail]    -> {to}: {message}")


class WhatsAppNotifier:
    def send(self, to: str, message: str) -> None:
        print(f"   [whatsapp] -> {to}: {message}")


class FakeNotifier:
    """For tests. Sends nothing. Just remembers."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def send(self, to: str, message: str) -> None:
        self.sent.append((to, message))


# ============================================================================
# NOW WATCH WHAT THIS BUYS YOU
# ============================================================================

if __name__ == "__main__":
    print("\n1. Same use case, Gmail:")
    signup("sujal@example.com", GmailNotifier())

    print("\n2. Same use case, WhatsApp. Note: signup() was NOT edited.")
    signup("sujal@example.com", WhatsAppNotifier())

    print("\n3. Same use case, in a test. No internet. No email sent.")
    fake = FakeNotifier()
    signup("sujal@example.com", fake)
    assert fake.sent == [("sujal@example.com", "Welcome to MedReport!")]
    print("   test passed -- and it took 0 milliseconds")

    print("\n4. The rule, tested completely on its own:")
    assert is_valid_email("a@b.com") is True
    assert is_valid_email("nope") is False
    print("   rule works. no notifier needed at all.")

    print("\n" + "=" * 60)
    print("""
That is the entire idea.

    is_valid_email   =  DOMAIN     the rule
    Notifier         =  PORT       the need
    signup           =  USE CASE   the sequence
    Gmail/WhatsApp   =  ADAPTER    the how
    FakeNotifier     =  ADAPTER    the how, for tests

Three things just happened that are impossible in signup_bad():

  - you swapped Gmail for WhatsApp without touching signup()
  - you tested signup() without sending a real email
  - you tested the rule with no notifier existing at all

In MedReport it is the same five things, bigger names:

    classify()       =  DOMAIN     value + range -> band
    FileStorage      =  PORT       "I need to store bytes"
    UploadReport     =  USE CASE   hash, dedupe, store, enqueue
    R2Storage        =  ADAPTER    the how (Cloudflare)
    LocalDiskStorage =  ADAPTER    the how (your laptop)

The folders in app/ are just these five ideas, given their own drawers.
""")
