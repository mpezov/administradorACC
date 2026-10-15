(function () {
  const cfg = window.MIRROR_VIEWER_CONFIG || {};
  const state = document.getElementById("viewerState");
  const container = document.getElementById("apsViewer");

  function setState(text, error) {
    state.textContent = text;
    state.classList.toggle("error", !!error);
  }

  function start() {
    if (!cfg.derivativeUrn || !cfg.proxyEndpoint) {
      setState("Configuración del espejo incompleta", true);
      return;
    }

    setState("Conectando al espejo…");

    Autodesk.Viewing.Initializer({
      env: "AutodeskProduction2",
      // Este espejo v0.5 fue preparado únicamente para SVF clásico.
      // Forzamos derivativeV2 para evitar que streamingV2 intente
      // resolver el mismo URN por modeldata/SVF2.
      api: "derivativeV2",
      useCookie: false,
      shouldInitializeAuth: false,
      endpoint: new URL(cfg.proxyEndpoint, window.location.origin).href.replace(/\/$/, "")
    }, function () {
      const viewer = new Autodesk.Viewing.GuiViewer3D(container);
      const started = viewer.start();

      if (started > 0) {
        setState("No se pudo iniciar Viewer (" + started + ")", true);
        return;
      }

      viewer.setTheme("light-theme");
      setState("Cargando · cacheando recursos…");

      Autodesk.Viewing.Document.load(
        "urn:" + cfg.derivativeUrn,
        function (doc) {
          let node = null;

          if (cfg.viewableGuid) {
            const matches = doc.getRoot().search({ guid: cfg.viewableGuid });
            if (matches && matches.length) node = matches[0];
          }

          if (!node) node = doc.getRoot().getDefaultGeometry();

          if (!node) {
            setState("El derivative no contiene geometría 3D.", true);
            return;
          }

          viewer.loadDocumentNode(doc, node)
            .then(function () {
              window.apsViewer = viewer;
              setState("Espejo activo · nuevos recursos se guardan localmente");
            })
            .catch(function (err) {
              console.error(err);
              setState(
                "Error cargando geometría: " +
                (err && err.message ? err.message : err),
                true
              );
            });
        },
        function (code, message, errors) {
          console.error("Document.load", code, message, errors);
          setState(
            "Error espejo/APS " + code + ": " + (message || "Document.load"),
            true
          );
        }
      );
    });
  }

  start();
})();
