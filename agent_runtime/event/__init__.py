"""EventSession, scheduling, recognition, and lifecycle ownership.

The external Event scheduler chooses which Agent instance receives a decision
opportunity.  This package owns the world-level loop; no Agent owns it.
Concrete scheduling code is not implemented yet.
"""
