// Contrôleur d'onglets générique, sans dépendance — un conteneur [data-tabs]
// contient des boutons .tab[data-tab="x"] et des panneaux .tab-panel[data-panel="x"] ;
// au chargement, active le premier onglet de chaque conteneur.
(function () {
  function initTabs(root) {
    var tabs = root.querySelectorAll(".tab");
    var panels = root.querySelectorAll(".tab-panel");
    function activate(name) {
      tabs.forEach(function (t) {
        t.classList.toggle("active", t.dataset.tab === name);
      });
      panels.forEach(function (p) {
        p.classList.toggle("active", p.dataset.panel === name);
      });
    }
    tabs.forEach(function (t) {
      t.addEventListener("click", function () {
        activate(t.dataset.tab);
      });
    });
    if (!tabs.length) return;
    // Un fragment d'URL (#backup) sélectionne l'onglet correspondant : c'est
    // ce qui ramène l'utilisateur sur SON onglet après une action qui redirige
    // (suppression d'un enregistrement), au lieu du premier onglet.
    // `auChargement` : au premier appel, il FAUT un onglet actif, donc un
    // fragment qui ne désigne aucun onglet retombe sur le premier. Ensuite non :
    // un lien interne vers une ancre hors onglets (#deck-exemple) renvoyait
    // l'éditeur sur le premier onglet, perdant celui qu'il avait ouvert — sans
    // rien demander et sans trace (reprise de revue du 2026-09-27). Un fragment
    // sans onglet correspondant ne touche plus à la sélection ; l'absence de
    // fragment, elle, reste un retour à l'onglet par défaut.
    function syncToHash(auChargement) {
      var cible = (location.hash || "").slice(1);
      var demande = cible && root.querySelector('.tab[data-tab="' + CSS.escape(cible) + '"]');
      if (demande) {
        activate(cible);
      } else if (auChargement || !cible) {
        activate(tabs[0].dataset.tab);
      }
    }
    syncToHash(true);
    // Une navigation hash-only (Retour navigateur depuis #backup, lien interne
    // vers #backup) ne recharge pas la page : sans cette écoute, l'URL et
    // l'onglet surligné divergeaient (revue adversariale 2026-07-29).
    window.addEventListener("hashchange", function () { syncToHash(false); });
  }

  document.querySelectorAll("[data-tabs]").forEach(initTabs);
})();
