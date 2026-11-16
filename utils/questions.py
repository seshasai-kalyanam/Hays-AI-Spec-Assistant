questions = [
    # Introduction
    "Are solid wire ≤#10 and stranded wire ≥ #8 specified?",
    # 260519 Conductors
    "What are the color codes for A Phase, B Phase, and C Phase for 120/208V and 277/480V?",
    "Is MC cable specified?",
    "What insulation types are required for all Locations?",
    # 260529 Hangers and Supports                   
    "What are the hanger and support specifications for equipment racks (aluminum, powder coated tube steel, or dry fit only)?",
    "Is there a stud to stud requirement?",
    "What is the typical stud wall spacing (16\" OC or 24\" OC)?",
    "Are isolator springs required on suspended XFMRs?",
    "What is the minimum size of all thread rods?",
    "What type of channel is specified (Cadmium, Galvanized, Hot Dipped)?",
    "What are the requirements for spring steel supports?",
    "Are there any special application requirements for hangers and supports?",
    # 260533 Raceways & Boxes
    "What is the minimum raceway size?",
    "Is the specified HR conduit size mentioned for Raceways & Boxes, and what is the size?",
    "What is the maximum number of circuits allowed in a conduit for Raceways & Boxes?",
    "Is EMT utilization allowed for Raceways & Boxes?",
    "What are the requirements for EMT fittings for Raceways & Boxes?",
    "What is the maximum degree of bend allowed for Raceways & Boxes?",
    "Is IMC or RGC utilization allowed for Raceways & Boxes?",
    "Is a long-radius 90° (degree) bend required for Raceways & Boxes?",
    "Is conduit allowed in slabs for Raceways & Boxes?",
    "What is the maximum conduit size for Raceways & Boxes?",
    "Are sleeves required for raceways and boxes? If so, where and what type (water stop, standard, etc.)?",
    "What is the specification for box elevation above the finished ceiling?",
    "What types of overhead conduit (MC, EMT, IMC/GRC) are specified for Raceways & Boxes on Branch, Branch HR, and Feeders?",
    "Is Schedule 40 or Schedule 80 PVC conduit allowed for underground use for raceways and boxes? If so, where is each used?",
    "Is a long-radius 90° required for underground use for Raceways & Boxes?",
    "What type of conduit is allowed underground (PVC, GRC, IMC, RTRC)?",
    "What type of conduit stub up into equipment is required (PVC, GRC, PVC/GRC, ALUM, IMC, EMT) ?",
    "What type of exposed conduit riser is required (PVC, GRC, PVC/GRC, ALUM, IMC, EMT)?",
    "Can PVC penetrate the exterior building envelope below grade? If not, what conduit type and stub-out length are required?",
    "What types of seals are required at the exterior for Raceways & Boxes (including concrete, red dye, and metal detection tape)?",
    "Is steel reinforcement specified for Raceways & Boxes?",
    "What is the minimum clearance between communication and power in distribution boards?",
    "What type of distribution board (DB) chairs are specified?",
    "What method and type of underground (UG) expansion joints are required?",
    "What are the backfill types and coverage requirements for both the exterior and interior of the building?",
    "What are the conduit requirements for the exterior of the building where conduits pass through parking lots or similar areas?",
    "Are there any special application requirements for raceways and boxes?",
    # 260533 Boxes & Enclosures                                      
    "What is the preferred box elevation, if any?",
    "What types of drywall electrical device boxes are specified?",
    "What types of surface tile electrical device boxes are specified?", 
    "What types of masonry electrical device boxes are specified?",
    "For junction and pull boxes, is SCR or hinged type required?",
    "What ceiling branch junction/pull boxes are specified?",
    "What are the requirements for ceiling branch junction/pull boxes?",
    "What are the requirements for underground junction/pull boxes?",
    "What are the requirements for service and secondary boxes and enclosures?",
    "What are the branch power and lighting box and enclosure specifications?",
    "Are there any special application requirements for boxes and enclosures?",
    # 262200, 262300 & 262416 Switchgear
    "What are the switchgear specifications for panel mounting height?",
    "What type of equipment racks are specified (aluminum, powder coated tube steel, or dry fit only)?",
    "What are the rear clearance requirements for switchgear transformers (XFMRs)?",
    "Are switchgear transformers (XFMRs) allowed to be hung?",
    "Switchgear: Are common gutters allowed above panels? If so, what are the requirements for life safety equipment isolation?",
    "Are there any special requirements for switchgear?",
    # 280000 Security       
    "What are the security requirements for wall device boxes and wall riser conduit?",
    "Is conduit stub to accessible ceiling only required for security? If yes, specify.",
    "If no stub is required for security, is it cable tray or a full conduit system?",
    "Are through-wall sleeves above ceilings required for security?",
    # 284621 Fire Alarm   
    "What are the fire alarm requirements for wall device boxes and wall riser conduit?",
    "Is conduit stub to accessible ceiling only required for fire alarm?",
    "If no stub is required, is it Cable Tray or Full Conduit System for fire alarm?",
    "Are through wall sleeves above ceilings required for fire alarm?",                                   
    # 270000 Communication      
    "What are the communication requirements for wall device boxes and wall riser conduit?",
    "What is the maximum degree of bend allowed for communication conduit?",
    "Is conduit stub to accessible ceiling only required for communication?",
    "If no stub is required, is it Cable Tray or Full Conduit System for communication?",
    "Are through wall sleeves above ceilings required for communication?",
    "Are there any special requirements for communication boxes, conduit, or path?",
                               
    # Communications Underground
    "What types of conduit are specified for communications underground (GRC, PVC, FRE)?",
    "What types of 90° bends are specified for communications underground conduit (GRC, PVC, FRE)?",
    "Is long radius required for communications underground conduit? If yes, what size?",
    "What are the requirements for underground junction/pull boxes?",
    "Are there any special requirements for communications underground?",
    "Is magnetic tape (Mag Tape) required for communications underground?",
    "What is the minimum clearance required from electrical ducts for communications underground?",
    #274113 Audio Visual      
    "What are the audio visual requirements for wall device boxes and wall riser conduit?",
    "Is conduit stub to accessible ceiling only required for audio visual?",
    "If no stub is required, is it Cable Tray or Full Conduit System for audio visual?",
    # Healthcare & Medical Facilities (e.g., UTMB, Ben Taub)
    "What receptacles are required, and what color/markings?",
    "What are the specifications for the Life Safety, Critical, and Equipment branches of the emergency power system?",
    "Are isolated power systems specified?",
    "What are the raceway and wiring requirements?",

    # Hospitality, Gaming & Convention Centers (e.g., Marriott, Casinos, Baytown Convention Center)
    "What are the requirements for architectural lighting controls and dimming systems in public spaces?",
    "Are room energy management systems (GREMS) specified?",
    "Are under-floor raceways or specific poke-throughs required?",
    "What are the AV/IT and low voltage integration requirements?",

    # Higher Education & Laboratories (e.g., Texas A&M, UT Tyler, Yellowstone School)
    "What are the specific electrical requirements for fume hoods, lab benches, and specialized equipment?",
    "Are there medium voltage distribution or duct bank specifications?",
    "What are the specifications audiovisual and lighting systems?",

    # High-Rise & Multi-Family Residential (e.g., JP Morgan Chase, Greystar, Tower Life)
    "What are the specifications for busway or bus duct systems for high-rise power distribution?",
    "Are there specific fire pump electrical connections, routing, and emergency feed requirements?",
    "What are the sub-metering requirements?",

    # Industrial, Transit & Special Projects (e.g., LyondellBasell, HOU Hobby Airport, Cruise Terminal)
    "Are there Class I, Division 1 or 2 hazardous location wiring requirements?",
    "What are the lighting and power specifications for high-bay environments?",
    "What are the power requirements for security screening systems?",

    # Commented out legacy queries
    # "What are the transformer clearance requirements?",
    # "Switchgear: Are common gutters allowed above panels?",
    # "What are the security requirements for conduit and wall device boxes?",
    # "What are the fire alarm conduit and stub requirements?",
    # "What are the communication conduit bend and stub requirements?",
    # "What are the audio-visual conduit and stub requirements?",
    # "What are the requirements for communications underground conduit type and size?",
    # "Is magnetic tape required?",
    # "What is the minimum clearance from electrical ducts?"
]
