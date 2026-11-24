// Se carga antes de la hoja de estilo para respetar el tema desde la primera pintura.
(() => {
  "use strict";
  try {
    const prefs = JSON.parse(localStorage.getItem("radar9:v1:preferencias") || "{}");
    if (["claro", "oscuro"].includes(prefs.tema)) document.documentElement.dataset.tema = prefs.tema;
    if (["grande", "mayor"].includes(prefs.tamano)) document.documentElement.dataset.tamano = prefs.tamano;
  } catch { /* El lector también funciona con el almacenamiento deshabilitado. */ }
})();
