# Public release preparation

The canonical project page is [Fisicas/GammaKeeper](https://github.com/Fisicas/GammaKeeper). Changing the existing repository's visibility keeps that URL. The README banner and the application wordmark link to it.

## Publication decision

The owner has explicitly accepted public disclosure of detector serial numbers and the personal email address in this project. Those details are not publication blockers. Keep the existing history if its contents are acceptable; a fresh repository is not required solely to hide those identifiers.

Changing visibility exposes the code and history and makes Actions history and logs visible to everyone. Public copies and forks can persist after a later change back to private. See [GitHub's visibility documentation](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/managing-repository-settings/setting-repository-visibility).

Before changing visibility:

1. Merge the reviewed display/cache changes through pull requests and pass Windows CI. A visibility change does not publish uncommitted work or merge feature branches.
2. Check the material being published for credentials or confidential third-party material. The personal-information audit is not a complete secret scan. Use portable paths in user-facing instructions.
3. Keep the release labeled pre-1.0 and distinguish synthetic software checks, an ongoing physical experiment, and completed device/scientific qualification. No completed multi-day qualification is claimed by the screenshots.
4. Confirm installation instructions work from the chosen release, set the social-preview image in GitHub settings, and provide a short release note describing tested devices and remaining limits.

Publishing the repository does not restart or update an installed recorder. Side-by-side deployment and viewer updates remain separate operations.

## Screenshots

The README now features the owner's October 13, 2026 capture of GammaKeeper 0.7.3 viewing the ongoing charcoal run. A native SVG viewport embeds the original PNG bytes and excludes only the 24-pixel Windows title bar, which contained the development viewer title. Data, axes, readings and controls are unchanged. The disabled stop control correctly reflects the viewer's permissions. The original capture and earlier images remain preserved. See [image provenance](images/README.md).

The release remains pre-1.0. The ongoing physical run and synthetic software validation are distinct; no completed multi-day qualification is claimed. The owner changes public visibility after the update and Windows CI are complete. No visibility or release action should operate the detector or replace a running recorder.
