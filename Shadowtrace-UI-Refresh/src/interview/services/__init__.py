"""
Application services — orchestration above the three bands.

These modules compose intake (band 1), the live session (band 2) and
evaluation (band 3) for the HTTP API. They may import any band; no band imports
them, so the band boundaries tested elsewhere are unchanged.
"""
