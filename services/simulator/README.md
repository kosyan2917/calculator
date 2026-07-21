# Simulator service

Independent backend reserved for simulation functionality.

Public API prefix: `/api/simulator/*`.

Current contract:

- `GET /api/simulator/health`

Game mechanics are intentionally not implemented here yet. Shared mechanics should later be extracted into a reusable package instead of importing the ArtCalc web application.
