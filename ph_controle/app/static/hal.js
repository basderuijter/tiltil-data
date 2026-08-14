/* Plattegrond: ververst de kleuren van de vakken zonder de pagina te herladen,
   zodat dit scherm op een wandmonitor kan blijven staan. */
(function () {
  const INTERVAL_MS = 15000;
  const stempel = document.getElementById("ververst");

  async function ververs() {
    try {
      const antwoord = await fetch("/api/hal");
      if (!antwoord.ok) return;
      const gegevens = await antwoord.json();
      let gewijzigd = 0;

      Object.keys(gegevens.vakken).forEach(function (code) {
        const vak = document.querySelector('[data-vak="' + CSS.escape(code) + '"]');
        if (!vak) return;
        const nieuw = "vak vak-" + gegevens.vakken[code].toestand;
        if (vak.className !== nieuw) {
          vak.className = nieuw;
          gewijzigd += 1;
        }
      });

      if (stempel) {
        const nu = new Date();
        stempel.textContent =
          "bijgewerkt " +
          String(nu.getHours()).padStart(2, "0") +
          ":" +
          String(nu.getMinutes()).padStart(2, "0") +
          (gewijzigd ? " · " + gewijzigd + " vak(ken) gewijzigd" : "");
      }
    } catch (fout) {
      if (stempel) stempel.textContent = "verversen mislukt";
    }
  }

  setInterval(ververs, INTERVAL_MS);
  ververs();
})();
