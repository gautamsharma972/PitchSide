# Fabric setup (≈10 min, in the Fabric portal)
1. Create a workspace on the Fabric capacity (Bicep output `fabricCapacity`, param `fabricAdmins`) and a Lakehouse `pitchside`.
2. **Batch / Lakehouse:** set `FABRIC_WORKSPACE` and `FABRIC_LAKEHOUSE=pitchside` on the app, grant its managed identity
   *Contributor* on the workspace, then `POST /api/export`. Files land in `Files/pitchside/match_<seed>/`.
   In a notebook: `spark.read.option("header",True).csv("Files/pitchside/match_3/events.csv").write.saveAsTable("events")`.
   Build a Power BI semantic model (Direct Lake) over `events` for xG / win-probability visuals.
3. **Real-time:** create an Eventstream with source *Azure Event Hubs* (`match-events`, consumer group `fabric`),
   destination *Eventhouse* table `MatchEvents`. Run the queries in `queries.kql`. Press "Replay live" in the app to stream.
