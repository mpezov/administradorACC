(function () {
  const cfg = window.VIEWER_CONFIG || {};
  const state = document.getElementById("viewerState");
  const container = document.getElementById("apsViewer");

  function setState(text, error) {
    state.textContent = text;
    state.classList.toggle("error", !!error);
  }

  function tokenProvider(onTokenReady) {
    fetch(cfg.tokenUrl, { credentials: "same-origin" })
      .then(function (r) {
        return r.json().then(function (data) {
          if (!r.ok || !data.access_token) {
            throw new Error(data.detail || data.error || "No se pudo obtener token Viewer.");
          }
          return data;
        });
      })
      .then(function (data) {
        onTokenReady(data.access_token, data.expires_in || 3600);
      })
      .catch(function (err) {
        console.error(err);
        setState(err.message || String(err), true);
      });
  }

  function start() {
    if (!cfg.derivativeUrn) {
      setState("Derivative URN no disponible", true);
      return;
    }

    const fmt = String(cfg.derivativeFormat || "").toUpperCase();
    const init = {
      env: fmt === "SVF" ? "AutodeskProduction" : "AutodeskProduction2",
      getAccessToken: tokenProvider
    };
    if (fmt !== "SVF") init.api = "streamingV2";

    setState("Autenticando…");
    Autodesk.Viewing.Initializer(init, function () {
      const viewer = new Autodesk.Viewing.GuiViewer3D(container);
      const started = viewer.start();
      if (started > 0) {
        setState("No se pudo iniciar Viewer (" + started + ")", true);
        return;
      }

      viewer.setTheme("light-theme");
      setState("Cargando modelo…");

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
            setState("El derivative no contiene geometría 3D predeterminada.", true);
            return;
          }

          viewer.loadDocumentNode(doc, node)
            .then(function () {
              window.apsViewer = viewer;
              setState("Modelo cargado");
            })
            .catch(function (err) {
              console.error(err);
              setState("Error cargando geometría: " + (err && err.message ? err.message : err), true);
            });
        },
        function (code, message, errors) {
          console.error("Document.load", code, message, errors);
          setState("Error APS " + code + ": " + (message || "Document.load"), true);
        }
      );
    });
  }

  start();
})();
