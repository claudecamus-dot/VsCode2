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
    if (tabs.length) activate(tabs[0].dataset.tab);
  }

  document.querySelectorAll("[data-tabs]").forEach(initTabs);
})();
