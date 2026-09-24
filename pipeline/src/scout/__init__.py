"""Camera Tech Scout pipeline.

The pipeline follows a strict evidence-first order:

    Evidence -> Problem / Mechanism / Effect -> Candidate -> Critic -> Discovery

Nothing downstream of ``evidence`` is allowed to introduce a claim that is not
anchored to a concrete evidence record.  When a field cannot be grounded the
pipeline writes one of the explicit markers defined in ``scout.models``
(``UNKNOWN``, ``LOW_CONFIDENCE``, ``NEEDS_VERIFICATION``) instead of guessing.
"""

__version__ = "0.1.0"
