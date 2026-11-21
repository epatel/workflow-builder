-- 009_workflow_description: free-form markdown notes for people reading the workflow
-- (purpose, references, links). Shown on the workflow page; never sent into a prompt.
ALTER TABLE workflows ADD COLUMN description TEXT NOT NULL DEFAULT '';
