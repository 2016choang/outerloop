"""The default guidance pack: the kernel's research TASTE, as advisory text.

Kernel agent-facing text carries only MECHANICS (docs/design/
agent-environment.md, "The rule"); how to do research is guidance, rendered
last and labelled advisory. These constants are the pack a contract gets
with `guidance: default` (the default). `none` ships nothing; a
repo-relative directory ships that repository's own files (`author.md`,
`panel.md`, read from the same ref as the contract).

The one-idea rule is deliberately absent: it was kernel-enforced taste (the
panel's blocking mandate) before the split, and a repository that wants it
writes it into its guidance files — as advice, never as a blocking rule.
"""

from outerloop.style import PLAIN_STYLE

AUTHOR = (
    "Form ONE concrete hypothesis for why the benchmark underperforms "
    "before you implement, and implement that one.\n\n"
    "Read past reports critically: a negative settles only what was actually "
    "run. One point in a parameter space, an eval that hit its walltime, or "
    "an infrastructure failure does not close an idea — vary what went "
    "untested, or rerun what failed for reasons that were not the idea's. "
    "What it does settle, do not repeat unchanged; build on it, or "
    "contradict it with a reason.\n\n"
    "Check the sibling view before choosing a direction and again before a "
    "submit, and prefer a direction no sibling is on unless you have a "
    "distinct angle.\n\n"
    "Spend your launch and sleep budgets as your judgment says; they are "
    "generous, not a target to exhaust.\n\n"
    "Sweep a hyperparameter or size in one array launch rather than one "
    "value per attempt, and show the landscape around the value you pick.\n\n"
    "Keep only the changes you still need: each merge and extraction is "
    "harder when your line's divergence from the base branch is large.\n\n"
    "Write the report short, with the most promising next step. A negative "
    "result reported clearly is a success, and so is an idea with a clear "
    "mechanism that does not yet beat the best or needs other "
    "hyperparameters to get there: report it, and your research line keeps "
    "it for a later attempt to build on.\n\n"
    "## How to write\n\n" + PLAIN_STYLE
)

PANEL = (
    "The aggregation standard for this repository: a few changes may be "
    "combined when the joint effect is genuine and the report documents each "
    "change's own effect; an even blend of small terms that buys a number "
    "without saying what works is not publishable. Check the ablations and "
    "say which component carries the win, or that none does.\n\n"
    "The landscape standard: a hyperparameter or capacity change (width, "
    "depth, learning rate, EMA, schedule) chosen at a single point should "
    "come with a picture of the landscape (a sweep, neighbouring values, or "
    "an ablation) showing why this value; judge how much picture the claim "
    "needs. A win that comes only from a larger model should argue why size "
    "is the right lever for this metric.\n\n"
    "Generality, robustness and mechanism claims in the report are worth an "
    "advisory finding when the provided evidence does not back them, and so "
    "is a claimed delta unconvincing at its size.\n\n"
    "Raise everything in this section only as advisory findings — the "
    "kernel's validity rules decide what blocks."
)
