"""Broadcast Agent type boundary.

Broadcast will follow the shared AgentLoop lifecycle over committed events and
viewer/buffer state, returning BroadcastPlan values. It cannot alter world
facts or invoke the Render backend directly. Concrete code is pending.
"""
