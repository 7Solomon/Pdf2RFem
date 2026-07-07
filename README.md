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
5. **Plan-Snap (G)**: Der Cursor faengt zusaetzlich, was schon im Plan
   gezeichnet ist - Schnittpunkte vorhandener Linien (magenta),
   Linienenden (hellblau), Punkt-auf-Linie (violett). Bei Vektor-PDFs
   exakt aus den PDF-Pfaden; bei gescannten Plaenen per OpenCV-
   Eckenerkennung (orange) im Ausschnitt um den Cursor.
6. **T** Linie abgreifen: vorhandene Plan-Linie anklicken -> wird als
   Geometrie uebernommen (nur Vektor-PDFs).
7. **F** Flaeche aufnehmen: in eine gefuellte Flaeche (z.B. Grauton)
   klicken -> Umriss wird per Flood-Fill erkannt, vereinfacht und auf
   exakte Vektorecken gezogen; **Enter** uebernimmt als geschlossenes
   Polygon, **+/-** aendert die Farbtoleranz. Fuer grosse Flaechen erst
   so zoomen, dass die Flaeche komplett sichtbar ist.
8. **F5** - nach RFEM uebertragen (mit Vorschau). Wiederholte Uebertragung
   aktualisiert vorhandene Objekte statt sie zu duplizieren.
9. **Strg+S** - Projekt als JSON speichern (inkl. RFEM-Zuordnungen).

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
    geometry.py    GeoPoint/GeoPolyline, GeometryModel
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
- [x] Linie abgreifen (T), Flaeche aufnehmen per Grauton-Flood-Fill (F)
- [ ] Geschlossene Polygone als RFEM-Surfaces uebertragen
      (Dicke wird in RFEM nachgepflegt)
- [ ] Validierung vor Transfer (Duplikat-Knoten mit Toleranz, offene Zuege)
- [ ] Lupe am Cursor, numerische Laengen-/Winkeleingabe beim Zeichnen
- [ ] Linienzug-Verfolgung (ganze Kette statt Einzelsegment abgreifen)
- [ ] Konstruktionslinien, Spiegeln/Reihen (Pfeiler), DXF-Export
- [ ] OCR der Bemassungstexte als Massstabs-/Laengenkontrolle
- [ ] 3D-Vorschau (PyVista)
