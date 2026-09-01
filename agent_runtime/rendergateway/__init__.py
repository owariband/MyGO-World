"""Outbound boundary from committed render jobs to the Render backend.

Only this boundary may deliver immutable RenderJob values and consume playback
status.  Agent packages cannot control WebGAL/MyGO directly.
"""
