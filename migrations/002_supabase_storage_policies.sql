-- Authenticated users may access only objects stored below their own user id.
-- The bucket itself stays private; unauthenticated/public reads remain denied.
CREATE POLICY "authenticated users insert own videos"
ON storage.objects FOR INSERT TO authenticated
WITH CHECK (
  bucket_id = 'understandvideo-private'
  AND (storage.foldername(name))[1] = (SELECT auth.uid()::text)
);

CREATE POLICY "authenticated users read own videos"
ON storage.objects FOR SELECT TO authenticated
USING (
  bucket_id = 'understandvideo-private'
  AND (storage.foldername(name))[1] = (SELECT auth.uid()::text)
);

CREATE POLICY "authenticated users update own videos"
ON storage.objects FOR UPDATE TO authenticated
USING (
  bucket_id = 'understandvideo-private'
  AND (storage.foldername(name))[1] = (SELECT auth.uid()::text)
)
WITH CHECK (
  bucket_id = 'understandvideo-private'
  AND (storage.foldername(name))[1] = (SELECT auth.uid()::text)
);

CREATE POLICY "authenticated users delete own videos"
ON storage.objects FOR DELETE TO authenticated
USING (
  bucket_id = 'understandvideo-private'
  AND (storage.foldername(name))[1] = (SELECT auth.uid()::text)
);

INSERT INTO public.schema_migrations(version)
VALUES (2)
ON CONFLICT DO NOTHING;
