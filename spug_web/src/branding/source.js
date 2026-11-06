// Modified distribution source offer by K, 2026-10-27. AGPL-3.0.
export const SOURCE_REPOSITORY = 'https://github.com/851855817/spug';
export const RELEASE_REVISION = process.env.REACT_APP_RELEASE_REVISION;

export function sourceLinks(revision) {
  if (typeof revision !== 'string' || !/^[0-9a-f]{40}$/.test(revision)) return null;
  return {revision, tree: `${SOURCE_REPOSITORY}/tree/${revision}`,
    archive: `${SOURCE_REPOSITORY}/archive/${revision}.tar.gz`};
}
