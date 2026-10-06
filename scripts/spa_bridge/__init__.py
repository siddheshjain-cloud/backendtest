"""Bridge: register SPA-ingested, Drive-stored documents into the M1
Document Library.

Every module in this package is read-only with respect to the SPA side
(``C:\\SPA``, outside any Git repo): the Google Sheet manifest, the Drive
files, and SPA's own ``SqliteManifestRepository`` mirror are never written
to. The only write path this package uses is M1's own
``DocumentLibraryService.create_document``, the same call the admin HTTP
API makes.
"""
