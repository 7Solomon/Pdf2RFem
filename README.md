# PDF2RFEM

Geometrie (Punkte, Polylinien) aus massstaeblichen PDF-Plaenen abgreifen und
per `dlubal.api` (gRPC) als Nodes/Lines an ein laufendes RFEM 6 uebertragen.

## Start

```
.venv\Scripts\python.exe -m pdf2rfem
```

Voraussetzungen: RFEM 6 laeuft lokal (gRPC-Server Port 9000), Dlubal-API-Key
ist in `%LOCALAPPDATA%\Dlubal\api\config.ini` hinterlegt (Standard von
`dlubal.api`) oder in der Umgebungsvariable `PDF2RFEM_API_KEY`.

## Arbeitsablauf

1. **Strg+N** - PDF-Plan oeffnen; Dialog fuer die erste *Ansicht* erscheint
   (Name, PDF-Seite, Massstab z.B. 1:50, Zeichenebene XY/XZ/YZ).
2. **R** - Referenzpunkt: bekannten Punkt im Plan anklicken und seine
   RFEM-Zielkoordinaten (m) eingeben. Erst danach ist Zeichnen freigegeben.
3. **M** - optional Massstab pruefen: zwei Punkte einer bemassten Strecke
   klicken, Sollmass eingeben; bei > 0,5 % Abweichung warnt das Tool
   (PDF vermutlich nicht massstabsgetreu exportiert).
4. **P** Punkte setzen, **L** Polylinien zeichnen
   (Klick = Vertex, **Shift** = Ortho, **Enter**/Doppelklick = fertig,
   **C** = geschlossen, **Ruecktaste** = Vertex zurueck, **Esc** = abbrechen).
   Snap auf vorhandene Punkte laeuft automatisch - gesnappte Punkte werden
   in RFEM zum selben Knoten.
5. **B** Kreisbogen zeichnen: Startpunkt, Endpunkt, dann einen Punkt auf
   dem Bogen klicken. Wird in RFEM zur echten Arc-Linie.
6. **Plan-Snap (G)**: Der Cursor faengt zusaetzlich, was schon im Plan
   gezeichnet ist - Schnittpunkte vorhandener Linien (magenta),
   Linienenden (hellblau), Punkt-auf-Linie (violett). Bei Vektor-PDFs
   exakt aus den PDF-Pfaden; bei gescannten Plaenen per OpenCV-
   Eckenerkennung (orange) im Ausschnitt um den Cursor.
7. **T** Abgreifen: vorhandene Plan-Linie ODER Plan-Kreisbogen anklicken
   -> wird als Geometrie uebernommen (nur Vektor-PDFs). Kreise/Boegen
   werden aus den Bezier-Pfaden des PDFs rekonstruiert (Kreis-Fit +
   Verkettung); ein Vollkreis wird als zwei Halbboegen uebernommen.
8. **F** Flaeche aufnehmen: in eine gefuellte PDF-Flaeche (z.B. Grauton)
   klicken -> Umriss wird per Flood-Fill erkannt, vereinfacht und auf
   exakte Vektorecken gezogen; **Enter** uebernimmt als geschlossenes
   Polygon, **+/-** aendert die Farbtoleranz. Fuer grosse Flaechen erst
   so zoomen, dass die Flaeche komplett sichtbar ist.
8b. **K** Fuellen (wie in Paint, aber auf der EIGENEN Geometrie): Klick in
   einen Bereich, der von eigenen Linien/Boegen umschlossen ist -> der
   kleinste geschlossene Zug um den Klickpunkt wird zur FLAECHE
   (GeoSurface). Die Flaeche referenziert ihre Randobjekte - genau wie
   RFEM-Surfaces (boundary_lines) - und wird schraffiert dargestellt;
   Bogenraender sind dabei voll unterstuetzt. Innere geschlossene Zuege
   werden automatisch als AUSSPARUNGEN (Cutouts) abgezogen und in RFEM zu
   Opening-Objekten - so bleibt z.B. der Hohlraum eines Kastenquerschnitts
   frei. Wird eine bereits (ohne Loch) gefuellte Flaeche erneut gefuellt,
   werden die Aussparungen nachgetragen. Linien zaehlen nur als verbunden,
   wenn sie denselben Knoten teilen - dank Knoten-Wiederverwendung (s.u.)
   passiert das beim Zeichnen automatisch. Klafft eine Luecke (lose
   Linienenden), warnt die Statuszeile - dann wird das betroffene Loch
   nicht erkannt. Loeschen einer Randlinie loescht die abhaengige Flaeche
   mit (undo-faehig).
9. **A** Lupe: vergroessertes Fenster oben rechts folgt dem Cursor
   (scharf nachgerendert, inkl. eigener Geometrie und Fadenkreuz);
   weicht automatisch zur anderen Ecke aus, wenn der Cursor ihr nahekommt.
10. **F5** - nach RFEM uebertragen (mit Vorschau). Wiederholte Uebertragung
    aktualisiert vorhandene Objekte statt sie zu duplizieren; Boegen als
    RFEM-Arc (2 Knoten + Kontrollpunkt), Flaechen als RFEM-Surface ueber
    ihre Randlinien, Aussparungen als RFEM-Opening (Dicke/Material danach
    in RFEM zuweisen).
11. **Strg+S** - Projekt als JSON speichern (inkl. RFEM-Zuordnungen).

Die Toolbar ist gruppiert: Datei | Zeichnen (Auswahl, Punkt, Polylinie,
Bogen) | Abgreifen (Linie/Bogen, Flaeche, Fuellen) | Einrichten (Referenz,
Messen) | Anzeige (Plan-Snap, Lupe, Einpassen) | Verlauf | RFEM.

**Knoten-Wiederverwendung:** Alle Werkzeuge (Polylinie, Bogen, Abgreifen,
Flaeche) verwenden beim Erzeugen von Punkten denselben Fangmechanismus:
liegt in Bildschirmnaehe (ca. 12 px, gedeckelt auf 3 PDF-Punkte) schon ein
Knoten, wird er referenziert statt dupliziert. Zwei nacheinander
abgegriffene Nachbarkanten eines Polygonzugs treffen sich dadurch
automatisch in EINEM Knoten - wichtig fuer zusammenhaengende FEM-Modelle
und Voraussetzung fuer das Fuellen-Werkzeug.

Weitere Bedienung: Mausrad = Zoom, mittlere Maustaste = Pan, Strg+0 =
einpassen, **S** = Auswahl-Werkzeug, Entf = Loeschen, Strg+Z/Y = Undo/Redo.

Mehrere Ansichten pro Plan (Grundriss + Schnitte, je eigene Ebene, eigener
Referenzpunkt, eigener Massstab) teilen sich ein Objektmodell - so setzt man
die 3D-Geometrie aus 2D-Ansichten zusammen.

## Architektur

```
pdf2rfem/
  core/        Qt-freie Logik (per pytest getestet)
    transform.py   Massstab, Workplane, ViewTransform (PDF-Punkt -> RFEM-m)
    geometry.py    GeoPoint/GeoPolyline/GeoArc, GeometryModel
    arcs.py        Kreisbogen-Geometrie (3-Punkt-Definition wie RFEM)
    faces.py       Fuellen: kleinster geschlossener Linienzug um einen Punkt
    commands.py    Undo/Redo (Command-Pattern), auch fuer Referenzpunkte
    snap.py        SnapEngine (Punkt-Snap, Ortho)
    project.py     Projekt, Ansichten, JSON-Serialisierung
  infra/
    pdf_document.py    PyMuPDF; Basis- + Detail-Ausschnitt-Rendering
    pdf_vector.py      Vektorsegmente aus dem PDF + raeumlicher Index
    edge_detect.py     OpenCV: Ecken-Fallback (Scans), Flaechen-Flood-Fill
    plan_snap.py       kombinierter Plan-Snap-Provider (Vektor > Raster)
    rfem_connector.py  dlubal.api; Transferplan, idempotente Uebertragung
  gui/         PySide6: Canvas, Tools, Hauptfenster, Dialoge
scripts/rfem_smoke_test.py   Verbindungstest (Wegwerf-Modell, wird nicht gespeichert)
tests/                       pytest-Suite fuer die Kernlogik
```

Objekte speichern Plan-Koordinaten + Ansicht, nie fertige RFEM-Koordinaten:
Aenderungen an Referenzpunkt/Massstab rechnen alles automatisch neu.

## Roadmap

- [x] MVP: PDF, Ansichten, Referenzpunkt, Punkte/Polylinien, Snap/Ortho,
      Undo/Redo, RFEM-Transfer (idempotent), Projekt-JSON, Massstabspruefung
- [x] Plan-Snap: Vektor-Schnittpunkte/-Endpunkte aus PDF-Pfaden,
      OpenCV-Ecken-Fallback fuer Scans
- [x] Linie/Bogen abgreifen (T), Flaeche aufnehmen per Grauton-Flood-Fill (F)
- [x] Kreisboegen: Erkennung aus PDF-Beziers, Zeichenwerkzeug (B),
      RFEM-Transfer als Arc-Linie
- [x] Lupe am Cursor (A), gruppierte Toolbar mit Icons
- [x] Knoten-Wiederverwendung ueber alle Werkzeuge (keine Duplikat-Knoten)
- [x] Fuellen-Werkzeug (K): Klick in umschlossenen Bereich -> Flaeche
      (auch mit Bogenrand)
- [x] Flaechen als RFEM-Surfaces ueber Randlinien uebertragen
      (Dicke wird in RFEM nachgepflegt)
- [x] Aussparungen/Cutouts: Loecher automatisch erkennen (Fuellen) und als
      RFEM-Openings uebertragen; Warnung bei losen Linienenden
- [ ] Validierung vor Transfer (Duplikat-Knoten mit Toleranz, offene Zuege)
- [ ] Luecken automatisch schliessen (lose Enden zusammenfuehren)
- [ ] Numerische Laengen-/Winkeleingabe beim Zeichnen
- [ ] Linienzug-Verfolgung (ganze Kette statt Einzelsegment abgreifen)
- [ ] Konstruktionslinien, Spiegeln/Reihen (Pfeiler), DXF-Export
- [ ] OCR der Bemassungstexte als Massstabs-/Laengenkontrolle
- [ ] 3D-Vorschau (PyVista)
