"""The first run, in a home that has never run any of this.

A disposable environment gives Claude Code a home it has never seen, and a
first run asks before it works: finish onboarding and sign in, trust this
folder, accept the permission mode it was launched with, allow the external
imports this project's own instructions declare. Each question is a modal
the dispatch cannot answer, so the agent holds the task unsent and the
execution plane reports a turn that never started — the same symptom as a
worker that died, with none of the cause.

The application the agents work inside asks several of its own. A freshly
provisioned environment opens Orca on its first-run wizard, modal over the
whole window. Those do not block orchestration — a complete two-agent cycle
was observed running from behind the wizard — but they cost the run the only
thing a picture of the screen is for: every frame of a settled run showed an
overlay rather than the agents' panels. They are answered here because they
are the same kind of question, asked of the same fresh home, at the same
moment, and decided by named fields in the same file.

Closing the wizard uncovered the next three, and one of them was caused by
the closure itself. The application decides whether a profile predates its
analytics release by asking whether the profile file already exists when it
starts; a seed written before the launch makes that answer yes, and a profile
that predates the release with no recorded answer is owed the consent banner.
So the first fix bought the wizard and sold a banner. The rest were simply
next in a queue the wizard had been standing in front of.

What makes this a short list rather than an endless one is that the
application enumerates it. The tips are a fixed catalogue compiled into the
bundle, and the bundle carries a single routine that closes the flow and
marks the whole catalogue seen in one go. So the question is not "what else
might appear" but "what does that catalogue contain", and it contains three
entries, all seeded here.

The answers are recorded state, not credentials. They are written here from
what the configuration already says and from what the applications themselves
write, rather than copied from the host, so no account, machine or history
travels into the environment with them.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from . import auth
from .adapters.base import BackendAdapter, EnvironmentHandle
from .config import RunConfig
from .result import FirstRunRecord
from .status import PhaseStatus

#: Where Claude Code keeps what it has been told, relative to the home.
STATE_RELATIVE = ".claude.json"
SETTINGS_RELATIVE = auth.SETTINGS_RELATIVE

#: Where Orca keeps the profile it starts in, relative to the home. It is
#: plain JSON, read once at startup; the store then holds that state in memory
#: and flushes it back over the file. So a seed written after the application
#: has been launched is overwritten by the very state it was meant to replace,
#: and this has to land before the launch to mean anything.
PROFILE_RELATIVE = ".config/orca/profiles/local-default/orca-data.json"

#: The key in that profile the wizard's visibility is decided from, and what
#: the application itself writes under it when somebody finishes the flow by
#: hand. Every one of these is a constant out of the application's own bundle.
ONBOARDING_KEY = "onboarding"
ONBOARDING_FLOW_VERSION = 4
ONBOARDING_OUTCOME = "completed"
ONBOARDING_LAST_STEP = 5

#: The two other top-level keys of that same profile. The load path reads the
#: file, strips a couple of retired entries, and spreads whatever object it
#: finds under each of these over the application's own defaults without
#: validating it — the same unguarded merge the onboarding seed already rides.
UI_KEY = "ui"
SETTINGS_KEY = "settings"

#: The catalogue of first-run tips, and the key the application filters it by.
#: The catalogue is a fixed array in the bundle, not something fetched, and the
#: picker is one line: keep the tips whose id is in neither the seen set nor
#: the completed set, and open the first survivor. So a seed naming every id in
#: the catalogue leaves the picker nothing to return, and that is the whole
#: difference between answering this overlay and answering only the one that
#: happened to be first. Seeding just the tip a capture photographed would
#: promote the next entry, and the capture after that would photograph that.
FEATURE_TIPS_KEY = "featureTipsSeenIds"
FEATURE_TIP_IDS = ("orca-cli", "cmd-j-palette", "voice-dictation")

#: The repository-star prompt, and the pair of values the application itself
#: writes when it decides the prompt is finished with. Every path that could
#: raise it opens by returning on the first of them, so the first is the gate
#: and the second is there to leave a consistent record rather than to cause
#: anything. It is a toast rather than a modal, so it covers a corner of the
#: window instead of all of it — which is a difference in how much of the
#: capture it costs, not in whether it costs any.
STAR_NAG_COMPLETED_KEY = "starNagCompleted"
STAR_NAG_DEFERRED_KEY = "starNagDeferredUntil"
STAR_NAG_COMPLETED = True
STAR_NAG_DEFERRED_UNTIL = None

#: The consent banner, and the two fields its one predicate reads: it appears
#: for a profile that is marked as predating the analytics release and has no
#: recorded answer. A per-run home is neither. The cohort is false because the
#: bundle's own classifier reads exactly that value as a fresh install, and the
#: answer is a refusal because a disposable environment provisioned by a runner
#: has nobody in it who could have agreed to anything.
#:
#: The identifier the application keeps beside these is deliberately not
#: written. Leaving it out is what makes the application mint its own on load,
#: which is better than this runner inventing one: an identifier invented here
#: would be a value that is neither a constant of the bundle nor a moment of
#: this run, and the rule for this file is that it writes neither.
TELEMETRY_KEY = "telemetry"
TELEMETRY_EXISTED_BEFORE_RELEASE = False
TELEMETRY_OPTED_IN = False

# On `orca-profile-index.json`, the file beside the profiles that says which
# one is selected: it is deliberately not written, and that is a decision
# rather than an omission. Whether it must already exist for `local-default`
# to be chosen on a cold start was never established. The bundle carries a
# factory that mints exactly that profile, so it is probably minted on a first
# start too — but probably is not a reading, and this module's whole rule is
# that a document it writes is one whose shape it has read. The onboarding
# value and the load path that merges it over the defaults were read; the
# index's schema was not. A guessed index that selects the wrong profile, or
# no profile, is a worse failure than the wizard and a quieter one, because
# the seed would then be sitting in a profile nothing opens while the receipt
# says the question was answered. Not writing it costs nothing that is not
# already lost: if the factory does not mint the profile, the environment
# behaves exactly as it does today, and the capture shows it. So if a run
# still photographs the wizard with this seed on disk, the index is the next
# thing to read out of the bundle — not the next thing to guess at.

#: One line per question this state answers, for the receipt. Naming them is
#: the difference between a run that answered five prompts and a run that
#: silently pre-approved whatever might be asked.
QUESTIONS = (
    "onboarding, which otherwise offers a theme and an interactive sign-in",
    "trusting the project copy, by its path inside the environment",
    "the external imports the project's own instructions declare",
    "the permission mode the execution plane launches the agent with",
    "the application's own first-run wizard, which otherwise sits modal over "
    "the whole window and hides every agent panel from the screen capture",
    "the application's catalogue of first-run tips, every entry of it, which "
    "otherwise opens one modal per run in the same place the wizard was",
    "the consent banner the seeded profile itself makes the application owe, "
    "by looking to it like a profile that predates the analytics release",
    "the repository-star prompt, which otherwise sits over the corner of the "
    "window the right-hand agent panel is drawn in",
)


def state_document(project_path: str) -> dict[str, Any]:
    """Return the first-run state for one project copy."""
    return {
        "hasCompletedOnboarding": True,
        "projects": {
            project_path: {
                "hasTrustDialogAccepted": True,
                "hasClaudeMdExternalIncludesApproved": True,
                "hasClaudeMdExternalIncludesWarningShown": True,
            }
        },
    }


def settings_document(run_config: RunConfig) -> dict[str, Any]:
    """Return the per-run settings, including whatever auth declared there."""
    return {
        **auth.settings_document(run_config),
        "skipDangerousModePermissionPrompt": True,
    }


def onboarding_document(*, closed_at_ms: int | None = None) -> dict[str, Any]:
    """Return the profile seed that answers the application's own first run.

    The wizard's visibility is one comparison, against one field: a profile
    whose `closedAt` is null is a profile whose flow has not been closed, and
    a fresh profile's defaults set exactly that. So closing it is the whole
    fix, and the rest of this document is here to make the closure consistent
    rather than to cause it — these are the values the application writes for
    itself when somebody finishes the flow by hand, so a profile seeded here
    reads back as one that was answered rather than one that was tampered with.

    The checklist the defaults carry is deliberately left out. The load path
    merges this object over those defaults, so an omitted key keeps the
    application's own value; writing one would be this runner inventing
    profile state instead of reporting it.

    The timestamp is this run's own moment, and it is the only value here that
    is not a constant. Nothing is read from the operator's profile to build
    it, and nothing may be.
    """
    closed = int(time.time() * 1000) if closed_at_ms is None else int(closed_at_ms)
    return {
        ONBOARDING_KEY: {
            "flowVersion": ONBOARDING_FLOW_VERSION,
            "closedAt": closed,
            "outcome": ONBOARDING_OUTCOME,
            "lastCompletedStep": ONBOARDING_LAST_STEP,
        }
    }


def education_document() -> dict[str, Any]:
    """Return the profile seed that answers the overlays behind the wizard.

    Three values, each one the gate its overlay is decided by. The tip
    catalogue is named in full rather than by the entry a capture happened to
    show, because naming one entry promotes the next and buys a run exactly
    one clean frame.

    Nothing here records an interaction that did not happen. The application's
    own routine for this also stamps a first-interaction time against each
    feature it tracks; that is bookkeeping for its analytics, it gates none of
    these overlays, and inventing it would be this runner writing history.
    """
    return {
        FEATURE_TIPS_KEY: list(FEATURE_TIP_IDS),
        STAR_NAG_COMPLETED_KEY: STAR_NAG_COMPLETED,
        STAR_NAG_DEFERRED_KEY: STAR_NAG_DEFERRED_UNTIL,
    }


def telemetry_document() -> dict[str, Any]:
    """Return the profile seed that answers the consent banner.

    Both fields are constants, and the field the application would otherwise
    invent for itself is left to it. The refusal is the honest answer as well
    as the quiet one: nothing in a per-run home consented, so nothing in a
    per-run home should be reporting.
    """
    return {
        TELEMETRY_KEY: {
            "existedBeforeTelemetryRelease": TELEMETRY_EXISTED_BEFORE_RELEASE,
            "optedIn": TELEMETRY_OPTED_IN,
        }
    }


def profile_document(*, closed_at_ms: int | None = None) -> dict[str, Any]:
    """Return the whole profile seed: the wizard and everything behind it.

    Three top-level keys, because the overlays are decided from three of them.
    Each is merged over the application's defaults on load, so an omitted key
    keeps the application's own value and only the named fields move.
    """
    return {
        **onboarding_document(closed_at_ms=closed_at_ms),
        UI_KEY: education_document(),
        SETTINGS_KEY: telemetry_document(),
    }


def apply(
    *, run_config: RunConfig, adapter: BackendAdapter, handle: EnvironmentHandle
) -> FirstRunRecord:
    """Write the first-run state into the per-run home and report what it wrote.

    Called from the bootstrap phase, which is the last phase that runs before
    the identity phase launches the application — and the identity phase holds
    the only launch there is. That ordering is what makes the profile seed
    worth writing at all; see `PROFILE_RELATIVE`.
    """
    record = FirstRunRecord(
        target=str(Path(handle.home_path)),
        entries=[STATE_RELATIVE, SETTINGS_RELATIVE, PROFILE_RELATIVE],
        questions=list(QUESTIONS),
    )
    home = Path(handle.home_path)
    for relative, document in (
        (STATE_RELATIVE, state_document(handle.project_path)),
        (SETTINGS_RELATIVE, settings_document(run_config)),
        (PROFILE_RELATIVE, profile_document()),
    ):
        adapter.write_file(
            str(home / relative), json.dumps(document, indent=2) + "\n", mode=0o600
        )
    record.status = PhaseStatus.OK
    record.detail = (
        f"{len(record.entries)} first-run state file(s) written into the per-run "
        f"home, answering {len(record.questions)} question(s) a home that has "
        "never run this work is asked before anything can start"
    )
    return record
