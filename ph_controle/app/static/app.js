/* Controlescherm: scannen, corrigeren en de akkoordknop bewaken. */
(function () {
  const CONDITIES = [
    ["goed", "Goed"],
    ["beschadigd", "Beschadigd"],
    ["verkeerd_artikel", "Verkeerd artikel"],
    ["ontbreekt", "Ontbreekt"],
  ];

  const code = window.PH_CODE;
  const dataElement = document.getElementById("controle-data");
  const tabel = document.querySelector("#regeltabel tbody");
  const scanveld = document.getElementById("scanveld");
  const scanformulier = document.getElementById("scanformulier");
  const scanmelding = document.getElementById("scanmelding");
  const akkoordknop = document.getElementById("akkoordknop");
  const akkoordredenen = document.getElementById("akkoordredenen");

  let controle = JSON.parse(dataElement.textContent);

  function meld(tekst, soort) {
    scanmelding.textContent = tekst;
    scanmelding.className = "melding melding-" + (soort || "info");
  }

  function focusScan() {
    if (scanveld) {
      scanveld.focus();
      scanveld.select();
    }
  }

  function maakCel(inhoud, klasse) {
    const cel = document.createElement("td");
    if (klasse) cel.className = klasse;
    if (inhoud instanceof Node) cel.appendChild(inhoud);
    else cel.textContent = inhoud;
    return cel;
  }

  function tekenRegel(regel) {
    const rij = document.createElement("tr");
    rij.dataset.barcode = regel.barcode;
    if (regel.conditie !== "goed") rij.className = "regel-probleem";
    else if (regel.is_akkoord) rij.className = "regel-ok";

    const artikel = document.createElement("div");
    artikel.textContent = regel.omschrijving;
    if (regel.sku) {
      const sku = document.createElement("div");
      sku.className = "mini";
      sku.textContent = regel.sku;
      artikel.appendChild(sku);
    }
    rij.appendChild(maakCel(artikel));
    rij.appendChild(maakCel(regel.barcode, "mini"));

    const teller = document.createElement("span");
    teller.className =
      "tel " + (regel.aantal_geteld >= regel.aantal_verwacht ? "tel-compleet" : "tel-open");
    teller.textContent = regel.aantal_geteld + " / " + regel.aantal_verwacht;
    rij.appendChild(maakCel(teller, "rechts"));

    const knoppen = document.createElement("div");
    [["−", -1], ["+", 1]].forEach(function (paar) {
      const knop = document.createElement("button");
      knop.type = "button";
      knop.className = "regelknop";
      knop.textContent = paar[0];
      knop.style.marginRight = "4px";
      knop.addEventListener("click", function () {
        zetRegel(regel.barcode, { aantal: regel.aantal_geteld + paar[1] });
      });
      knoppen.appendChild(knop);
    });
    rij.appendChild(maakCel(knoppen));

    const keuze = document.createElement("select");
    CONDITIES.forEach(function (paar) {
      const optie = document.createElement("option");
      optie.value = paar[0];
      optie.textContent = paar[1];
      if (paar[0] === regel.conditie) optie.selected = true;
      keuze.appendChild(optie);
    });
    keuze.addEventListener("change", function () {
      zetRegel(regel.barcode, { conditie: keuze.value });
    });
    rij.appendChild(maakCel(keuze));

    const notitie = document.createElement("input");
    notitie.type = "text";
    notitie.value = regel.notitie || "";
    notitie.placeholder = "opmerking";
    notitie.style.width = "100%";
    notitie.addEventListener("change", function () {
      zetRegel(regel.barcode, { notitie: notitie.value });
    });
    rij.appendChild(maakCel(notitie));

    return rij;
  }

  function teken() {
    tabel.innerHTML = "";
    controle.regels.forEach(function (regel) {
      tabel.appendChild(tekenRegel(regel));
    });

    akkoordknop.disabled = !controle.mag_akkoord;
    akkoordredenen.innerHTML = "";
    if (controle.mag_akkoord) {
      const ok = document.createElement("div");
      ok.className = "melding melding-ok";
      ok.textContent =
        "Alles gecontroleerd: " +
        controle.totaal_geteld +
        " stuks in goede staat. Geef akkoord om de PH af te melden en de pakbon te printen.";
      akkoordredenen.appendChild(ok);
    } else {
      const blok = document.createElement("div");
      blok.className = "melding melding-let-op";
      blok.textContent = "Akkoord kan nog niet:";
      const lijst = document.createElement("ul");
      lijst.className = "lijstje";
      controle.redenen.forEach(function (reden) {
        const item = document.createElement("li");
        item.textContent = reden;
        lijst.appendChild(item);
      });
      blok.appendChild(lijst);
      akkoordredenen.appendChild(blok);
    }
  }

  async function stuur(pad, gegevens) {
    const antwoord = await fetch(pad, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(gegevens),
    });
    const inhoud = await antwoord.json().catch(function () {
      return {};
    });
    if (!antwoord.ok) {
      throw new Error(inhoud.fout || "Onbekende fout (" + antwoord.status + ")");
    }
    return inhoud;
  }

  async function zetRegel(barcode, wijziging) {
    try {
      const inhoud = await stuur(
        "/api/ph/" + encodeURIComponent(code) + "/regel",
        Object.assign({ barcode: barcode }, wijziging)
      );
      controle = inhoud.controle;
      teken();
      meld("Regel bijgewerkt.", "info");
    } catch (fout) {
      meld(fout.message, "fout");
    }
    focusScan();
  }

  async function verwerkScan(barcode) {
    try {
      const inhoud = await stuur("/api/ph/" + encodeURIComponent(code) + "/scan", {
        barcode: barcode,
      });
      controle = inhoud.controle;
      teken();
      const soort =
        inhoud.scan.status === "geteld"
          ? "ok"
          : inhoud.scan.status === "te_veel"
          ? "let-op"
          : "fout";
      meld(inhoud.scan.melding, soort);
    } catch (fout) {
      meld(fout.message, "fout");
    }
    focusScan();
  }

  scanformulier.addEventListener("submit", function (gebeurtenis) {
    gebeurtenis.preventDefault();
    const barcode = scanveld.value.trim();
    scanveld.value = "";
    if (barcode) verwerkScan(barcode);
  });

  // De scanner tikt razendsnel; het veld moet daarom vrijwel altijd focus houden.
  document.addEventListener("click", function (gebeurtenis) {
    const doel = gebeurtenis.target;
    if (doel.closest("input, select, textarea, button, a")) return;
    focusScan();
  });

  teken();
  focusScan();
})();
