/* Controlescherm: scannen, corrigeren, en zo min mogelijk handelingen.
   - Enter op een barcode telt één stuk.
   - Een getal + Enter zet de laatst gescande regel ineens op dat aantal.
   - F2 geeft akkoord; in snelmodus doet de laatste scan dat zelf.
   - Esc brengt de focus altijd terug naar het scanveld. */
(function () {
  const CONDITIES = [
    ["goed", "Goed"],
    ["beschadigd", "Beschadigd"],
    ["verkeerd_artikel", "Verkeerd artikel"],
    ["ontbreekt", "Ontbreekt"],
  ];

  const code = window.PH_CODE;
  const snelmodus = window.PH_SNELMODUS === true;
  const dataElement = document.getElementById("controle-data");
  const tabel = document.querySelector("#regeltabel tbody");
  const scanveld = document.getElementById("scanveld");
  const scanformulier = document.getElementById("scanformulier");
  const scanmelding = document.getElementById("scanmelding");
  const akkoordknop = document.getElementById("akkoordknop");
  const akkoordredenen = document.getElementById("akkoordredenen");
  const kratknoppen = document.getElementById("kratknoppen");

  let controle = JSON.parse(dataElement.textContent);
  let laatsteBarcode = null;
  let bezig = false;

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

    const telcel = document.createElement("div");
    const teller = document.createElement("span");
    teller.className =
      "tel " + (regel.aantal_geteld >= regel.aantal_verwacht ? "tel-compleet" : "tel-open");
    teller.textContent = regel.aantal_geteld + " / " + regel.aantal_verwacht;
    telcel.appendChild(teller);
    if (controle.kratten > 1) {
      // Bij meerdere dozen moet zichtbaar zijn wat waar in ging.
      const verdeling = document.createElement("div");
      (regel.per_krat || []).forEach(function (aantal, index) {
        if (!aantal) return;
        const merk = document.createElement("span");
        merk.className = "kratmerk";
        merk.textContent = "K" + (index + 1) + ": " + aantal;
        verdeling.appendChild(merk);
      });
      telcel.appendChild(verdeling);
    }
    rij.appendChild(maakCel(telcel, "rechts"));

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
    if (regel.aantal_verwacht > 1 && regel.aantal_geteld < regel.aantal_verwacht) {
      // Scheelt scannen bij meerdere stuks van hetzelfde artikel.
      const alles = document.createElement("button");
      alles.type = "button";
      alles.className = "regelknop regelknop-breed";
      alles.textContent = "alle " + regel.aantal_verwacht;
      alles.addEventListener("click", function () {
        zetRegel(regel.barcode, { aantal: regel.aantal_verwacht });
      });
      knoppen.appendChild(alles);
    }
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

  function tekenKratten() {
    if (!kratknoppen) return;
    kratknoppen.innerHTML = "";
    const totaal = controle.kratten || 1;
    for (let nummer = 1; nummer <= totaal + 1; nummer += 1) {
      const nieuw = nummer > totaal;
      const knop = document.createElement("button");
      knop.type = "button";
      knop.className =
        "kratknop" + (nummer === controle.actieve_krat ? " kratknop-actief" : "");
      knop.textContent = nieuw ? "+ krat " + nummer : "krat " + nummer;
      knop.addEventListener("click", function () {
        zetKrat(nummer);
      });
      kratknoppen.appendChild(knop);
    }
  }

  function teken() {
    tabel.innerHTML = "";
    controle.regels.forEach(function (regel) {
      tabel.appendChild(tekenRegel(regel));
    });
    tekenKratten();

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
      body: gegevens === undefined ? undefined : JSON.stringify(gegevens),
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
      laatsteBarcode = barcode;
      teken();
      meld("Regel bijgewerkt.", "info");
      await misschienAfronden();
    } catch (fout) {
      meld(fout.message, "fout");
    }
    focusScan();
  }

  async function zetKrat(nummer) {
    try {
      const inhoud = await stuur("/api/ph/" + encodeURIComponent(code) + "/krat", {
        krat: nummer,
      });
      controle = inhoud.controle;
      teken();
      meld("Krat " + nummer + ": alles wat je nu scant gaat in deze doos.", "info");
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
      if (inhoud.scan.status === "geteld") laatsteBarcode = barcode;
      teken();
      const soort =
        inhoud.scan.status === "geteld"
          ? "ok"
          : inhoud.scan.status === "te_veel"
          ? "let-op"
          : "fout";
      meld(inhoud.scan.melding, soort);
      await misschienAfronden();
    } catch (fout) {
      meld(fout.message, "fout");
    }
    focusScan();
  }

  async function geefAkkoord(automatisch) {
    if (bezig) return;
    if (!controle.mag_akkoord) {
      meld("Akkoord kan nog niet: " + controle.redenen.join(" "), "let-op");
      return;
    }
    bezig = true;
    meld(automatisch ? "Compleet — afmelden en pakbon printen…" : "Akkoord geven…", "ok");
    try {
      const inhoud = await stuur("/api/ph/" + encodeURIComponent(code) + "/akkoord");
      window.location.href = inhoud.pakbon_url;
    } catch (fout) {
      bezig = false;
      meld(fout.message, "fout");
      focusScan();
    }
  }

  function misschienAfronden() {
    // Snelmodus: de scan die de order compleet maakt, rondt hem ook af.
    // Niet bij een deellevering: daar is "alles geteld" al waar na de eerste
    // scan, en dan zou de doos vertrekken terwijl de rest nog op tafel ligt.
    if (snelmodus && controle.mag_akkoord && !controle.is_deellevering) {
      return geefAkkoord(true);
    }
    return Promise.resolve();
  }

  scanformulier.addEventListener("submit", function (gebeurtenis) {
    gebeurtenis.preventDefault();
    const invoer = scanveld.value.trim();
    scanveld.value = "";
    if (!invoer) return;

    // Alleen cijfers en kort: bedoeld als aantal voor de laatst gescande regel.
    if (/^\d{1,3}$/.test(invoer) && laatsteBarcode && !controle.regels.some(function (r) {
      return r.barcode === invoer;
    })) {
      zetRegel(laatsteBarcode, { aantal: parseInt(invoer, 10) });
      return;
    }
    verwerkScan(invoer);
  });

  document.addEventListener("keydown", function (gebeurtenis) {
    if (gebeurtenis.key === "F2") {
      gebeurtenis.preventDefault();
      geefAkkoord(false);
    } else if (gebeurtenis.key === "Escape") {
      focusScan();
    }
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
