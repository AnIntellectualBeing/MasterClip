# Product update

One primary workflow: search a GTA topic, select a verified completed YouTube livestream, choose horizontal highlights or vertical Shorts, and create videos. File uploads and pasted links are secondary paths. Settings and manual editing are collapsed by default.

Discovery must verify historical livestream metadata. Live, upcoming, ordinary uploads, and videos still processing after a stream are excluded. The optional YouTube Data API uses completed-event filtering; the default path uses public yt-dlp search plus archive checks. Dates are not invented when metadata lacks an end time. Discovery is asynchronous, cancellable, bounded, and cached briefly.

Horizontal renders preserve the entire source frame on a 1920×1080 canvas. No portrait crop or blur is used in landscape. Vertical renders use a 1080×1920 canvas. Captions are included by default and can be disabled. Spoken word times must survive transcript storage, cut mapping, and caption grouping; proportional sentence timing cannot substitute for actual available word times.

The automatic pipeline analyzes and exports without a second mandatory action. The compact editor allows optional clip selection and caption corrections, then rebuilds from the retained source. A captionless result must be identified when no reliable speech is detected rather than falsely marked captioned.

This repository is independent of the earlier prototype. Git excludes recordings, model caches, generated exports, user state, and credentials. Local installation and configuration are documented, with CI running deterministic media and API tests. GitHub publishing remains a separate user action.
