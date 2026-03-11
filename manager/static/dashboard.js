(() => {
  const branchMap = window.xttBranchMap || {};
  const repoDefaultBranchMap = window.xttRepoDefaultBranchMap || {};
  const repoProfileMap = window.xttRepoProfileMap || {};
  const globalDefaultBranch = window.xttDefaultBranch || "main";

  const renderRepoProfileHint = (repo, targetId) => {
    const target = document.getElementById(targetId);
    if (!target) {
      return;
    }

    const profile = repoProfileMap[repo] || {};
    const lines = [
      `stack: ${profile.stack || "generic"}`,
      `default_branch: ${profile.default_branch || globalDefaultBranch}`,
      `install: ${profile.install_cmd || "(none)"}`,
      `lint: ${profile.lint_cmd || "(none)"}`,
      `test: ${profile.test_cmd || "(none)"}`,
      `build: ${profile.build_cmd || "(none)"}`,
      `smoke: ${profile.smoke_test_cmd || "(none)"}`,
      `high_risk_paths: ${(profile.high_risk_paths || []).join(", ") || "(none)"}`,
      `needs_ui_evidence: ${profile.needs_ui_evidence ? "true" : "false"}`,
    ];
    target.textContent = lines.join(" | ");
  };

  const wireBranchSelect = (repoSelectId, branchSelectId, profileHintId) => {
    const repoSelect = document.getElementById(repoSelectId);
    const branchSelect = document.getElementById(branchSelectId);
    if (!repoSelect || !branchSelect) {
      return;
    }

    const renderBranches = () => {
      const repo = repoSelect.value;
      const repoDefault = repoDefaultBranchMap[repo] || globalDefaultBranch;
      const branches = branchMap[repo] && branchMap[repo].length ? branchMap[repo] : [repoDefault];
      const previous = branchSelect.dataset.selected || branchSelect.value || repoDefault;
      branchSelect.innerHTML = "";

      for (const branch of branches) {
        const option = document.createElement("option");
        option.value = branch;
        option.textContent = branch;
        if (branch === previous) {
          option.selected = true;
        }
        branchSelect.appendChild(option);
      }

      if (![...branchSelect.options].some((option) => option.selected)) {
        const defaultOption = [...branchSelect.options].find((option) => option.value === repoDefault);
        if (defaultOption) {
          defaultOption.selected = true;
        } else if (branchSelect.options.length > 0) {
          branchSelect.options[0].selected = true;
        }
      }

      branchSelect.dataset.selected = branchSelect.value || repoDefault;
      renderRepoProfileHint(repo, profileHintId);
    };

    repoSelect.addEventListener("change", () => {
      branchSelect.dataset.selected = repoDefaultBranchMap[repoSelect.value] || globalDefaultBranch;
      renderBranches();
    });
    branchSelect.addEventListener("change", () => {
      branchSelect.dataset.selected = branchSelect.value;
    });

    branchSelect.dataset.selected = repoDefaultBranchMap[repoSelect.value] || globalDefaultBranch;
    renderBranches();
  };

  wireBranchSelect("repo", "base_branch", "repo_profile_hint");
  wireBranchSelect("backlog_repo", "backlog_base_branch", "backlog_repo_profile_hint");
})();
