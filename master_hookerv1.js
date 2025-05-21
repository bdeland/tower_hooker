// --- Frida Master Hooking Script for "The Tower" (Refactored) ---
import "frida-il2cpp-bridge";

console.log("[MasterHooker] Script loading. Il2Cpp.perform() will be scheduled.");

// Function to send data to Python, adapting to new DB structure
// fridaType: 'round_start_package', 'round_end_package', 'round_snapshot_update', 'in_round_event', 'script_status'
function transmitData(fridaType, eventSubtype, dataObject = {}) {
    try {
        send({
            frida_type: fridaType,
            timestamp: new Date().toISOString(),
            event_subtype: eventSubtype, // For 'in_round_event' and 'script_status' primarily
            data: dataObject // Contains the actual payload for the fridaType
        });
        // Optional: console.log for debugging in Frida CLI
        // console.log(`[MasterHooker] Sent (${fridaType}): ${eventSubtype} - ${JSON.stringify(dataObject)}`);
    } catch (e) {
        console.error(`[MasterHooker] Error in transmitData: ${e.message}`);
    }
}

setTimeout(() => {
    console.log("[MasterHooker] [DELAYED START] Attempting Il2Cpp.perform() after 15s.");
    transmitData('script_status', 'il2cpp_perform_scheduled');

    Il2Cpp.perform(() => {
        console.log("[MasterHooker] Il2Cpp.perform() CALLBACK ENTERED.");
        transmitData('script_status', 'il2cpp_perform_callback_entered');

        // ===================================================================================
        // --- CONFIGURATION (Keep as is, these are specific to the game) ---
        // ===================================================================================
        const RVA_MAIN_START_NEW_ROUND = ptr("0x13B8BB4");
        const RVA_MAIN_GAME_OVER = ptr("0x13B74C8");
        const OFFSET_MAIN_CURRENT_TIER = ptr("0x5A8");
        const OFFSET_MAIN_CASH = ptr("0x108");
        const OFFSET_MAIN_COINS = ptr("0x110");
        const OFFSET_MAIN_GEMS = ptr("0x118");
        const OFFSET_MAIN_CURRENT_WAVE = ptr("0x148");
        const OFFSET_MAIN_STONES = ptr("0x11C"); // Example
        const OFFSET_MAIN_CELLS = ptr("0x124"); // Example

        const RVA_CARDS_CARD_PANEL_OPEN = ptr("0x21AE684");
        // ... (Card offsets) ...
        const OFFSET_CARDS_LEVEL_ARRAY = ptr("0x48");
        const OFFSET_CARDS_NAME_ARRAY = ptr("0x68");
        const OFFSET_CARDS_CURRENT_PRESET = ptr("0x120");
        const OFFSET_CARDS_SLOT_CARD_INT_2D = ptr("0x110");
        const OFFSET_CARDS_SLOT_CARD_ASSIGNED_BOOL_2D = ptr("0x108");
        const CONST_CARDS_SLOTS_PER_PRESET = 27;
        const CONST_ARRAY_ELEMENTS_START_OFFSET = (Process.arch === "arm64" || Process.arch === "x64") ? 0x20 : 0x10;
        const INTERVAL_SNAPSHOT_LOGGING_MS = 1000;

        // --- SYSTEM TYPES (Keep as is) ---
        let Il2CppSystemBoolean, Il2CppSystemInt32, Il2CppSystemString;

        // ===================================================================================
        // --- GLOBAL STATE ---
        // ===================================================================================
        let il2cppBase = ptr(0);
        let mainInstance = null;
        let cardsInstance = null; // For reading card data
        let isRoundActive = false;
        let snapshotIntervalId = null;

        // ===================================================================================
        // --- UTILITY FUNCTIONS (readField, initializeSystemTypes - Keep mostly as is) ---
        // ===================================================================================
        function readField(instance, offset, type) {
            if (!instance || instance.isNull()) {
                // transmitData('script_status', 'read_field_null_instance', { offset: offset.toString(), type: type });
                return null;
            }
            try {
                const fieldPtr = instance.handle.add(offset);
                switch (type) {
                    case "bool": return fieldPtr.readU8() !== 0;
                    case "int": return fieldPtr.readS32();
                    case "double": return fieldPtr.readDouble();
                    case "float": return fieldPtr.readFloat();
                    // ... other types from your original script ...
                    case "string":
                        const strPtr = fieldPtr.readPointer();
                        if (strPtr.isNull()) return null;
                        if (!Il2CppSystemString) { console.warn("[MasterHooker] readField: Il2CppSystemString not initialized."); return "[SysStrN/A]"; }
                        return new Il2Cpp.String(strPtr, Il2CppSystemString).content;
                    default:
                        transmitData('script_status', 'read_field_unknown_type', { type: type, offset: offset.toString() });
                        return null;
                }
            } catch (e) {
                transmitData('script_status', 'read_field_exception', { offset: offset.toString(), type: type, message: e.message });
                return null;
            }
        }

        function initializeSystemTypes() {
            try {
                Il2CppSystemBoolean = Il2Cpp.corlib.class("System.Boolean");
                Il2CppSystemByte = Il2Cpp.corlib.class("System.Byte");
                Il2CppSystemSByte = Il2Cpp.corlib.class("System.SByte");
                Il2CppSystemInt16 = Il2Cpp.corlib.class("System.Int16");
                Il2CppSystemUInt16 = Il2Cpp.corlib.class("System.UInt16");
                Il2CppSystemInt32 = Il2Cpp.corlib.class("System.Int32");
                Il2CppSystemUInt32 = Il2Cpp.corlib.class("System.UInt32");
                Il2CppSystemInt64 = Il2Cpp.corlib.class("System.Int64");
                Il2CppSystemUInt64 = Il2Cpp.corlib.class("System.UInt64");
                Il2CppSystemSingle = Il2Cpp.corlib.class("System.Single");
                Il2CppSystemDouble = Il2Cpp.corlib.class("System.Double");
                Il2CppSystemString = Il2Cpp.corlib.class("System.String");
                Il2CppSystemVoid = Il2Cpp.corlib.class("System.Void");
                transmitData('script_status', 'system_types_initialized');
            } catch (e) {
                transmitData('script_status', 'system_types_init_failed', { message: e.message });
                console.error("[MasterHooker] Error initializing system types: " + e.message);
            }
        }
        
        // --- CARD READING LOGIC (Keep your detailed logEquippedCards, but make it return data) ---

        function getEquippedCards(cardsInst) {
            if (!cardsInst || (cardsInst.isNull && typeof cardsInst.isNull === 'function' && cardsInst.isNull())) {
                transmitData('script_status', 'get_equipped_cards_called_with_null_instance');
                return { preset: -1, slots: [], error: "cardsInstance is null or invalid." };
            }
            if (!Il2CppSystemInt32 || !Il2CppSystemString || !Il2CppSystemBoolean) {
                transmitData('script_status', 'get_equipped_cards_system_types_not_ready');
                return { preset: -1, slots: [], error: "Required Il2Cpp System types not initialized for card reading." };
            }

            let equippedCardsData = { preset: -1, slots: [] }; // Initialize the object to be returned

            try {
                const mainCardLevelPtr = cardsInst.handle.add(OFFSET_CARDS_LEVEL_ARRAY).readPointer();
                const mainCardNamePtr = cardsInst.handle.add(OFFSET_CARDS_NAME_ARRAY).readPointer();

                let mainCardLevelArray = null;
                let mainCardName_ElementsBasePtr = null; // Pointer to the first element of the string array
                let mainCardName_ArrayLength = 0;

                if (!mainCardLevelPtr.isNull()) {
                    try {
                        // Assuming Il2Cpp.Array can take Il2CppSystemInt32 directly for primitive types
                        mainCardLevelArray = new Il2Cpp.Array(mainCardLevelPtr, Il2CppSystemInt32);
                    } catch (e) {
                        transmitData('script_status', 'get_equipped_cards_level_array_error', { message: e.message });
                        // mainCardLevelArray will remain null, processing will continue carefully
                    }
                } else {
                    // transmitData('script_status', 'get_equipped_cards_level_array_ptr_null');
                }

                if (!mainCardNamePtr.isNull()) {
                    try {
                        const tempNameArrayWrapper = new Il2Cpp.Array(mainCardNamePtr); // Generic array wrapper to get length and elements ptr
                        mainCardName_ArrayLength = tempNameArrayWrapper.length;
                        // Get the base pointer to the elements. For Il2Cpp.String[], elements are pointers to strings.
                        mainCardName_ElementsBasePtr = tempNameArrayWrapper.elements; // This should be Il2Cpp.Pointer
                        if (!mainCardName_ElementsBasePtr || typeof mainCardName_ElementsBasePtr.add !== 'function') { // Fallback if .elements is not a pointer
                            mainCardName_ElementsBasePtr = mainCardNamePtr.add(CONST_ARRAY_ELEMENTS_START_OFFSET);
                        }
                    } catch (e) {
                        transmitData('script_status', 'get_equipped_cards_name_array_error', { message: e.message });
                        // mainCardName_ElementsBasePtr will remain null
                    }
                } else {
                    // transmitData('script_status', 'get_equipped_cards_name_array_ptr_null');
                }

                const currentPreset = cardsInst.handle.add(OFFSET_CARDS_CURRENT_PRESET).readS32(); // Assuming it's a signed int
                equippedCardsData.preset = currentPreset;

                const slotCardInt_2D_ArrayPtr = cardsInst.handle.add(OFFSET_CARDS_SLOT_CARD_INT_2D).readPointer();
                const slotCardAssignedBool_2D_ArrayPtr = cardsInst.handle.add(OFFSET_CARDS_SLOT_CARD_ASSIGNED_BOOL_2D).readPointer();

                if (slotCardInt_2D_ArrayPtr.isNull() || slotCardAssignedBool_2D_ArrayPtr.isNull()) {
                    transmitData('script_status', 'equipped_cards_slot_arrays_null', { preset: currentPreset });
                    equippedCardsData.error = "Slot definition arrays are null.";
                    return equippedCardsData; // Critical data missing
                }

                let slotCardInt_ElementsBase = null;
                let slotCardAssignedBool_ElementsBase = null;

                try {
                    // These are flat arrays representing 2D data (preset * slots_per_preset + slot_index)
                    const tempIntWrapper = new Il2Cpp.Array(slotCardInt_2D_ArrayPtr);
                    slotCardInt_ElementsBase = tempIntWrapper.elements;
                    if (!slotCardInt_ElementsBase || typeof slotCardInt_ElementsBase.add !== 'function') {
                        slotCardInt_ElementsBase = slotCardInt_2D_ArrayPtr.add(CONST_ARRAY_ELEMENTS_START_OFFSET);
                    }

                    const tempBoolWrapper = new Il2Cpp.Array(slotCardAssignedBool_2D_ArrayPtr);
                    slotCardAssignedBool_ElementsBase = tempBoolWrapper.elements;
                    if (!slotCardAssignedBool_ElementsBase || typeof slotCardAssignedBool_ElementsBase.add !== 'function') {
                        slotCardAssignedBool_ElementsBase = slotCardAssignedBool_2D_ArrayPtr.add(CONST_ARRAY_ELEMENTS_START_OFFSET);
                    }
                } catch (e) {
                    transmitData('script_status', 'equipped_cards_slot_bases_error', { message: e.message });
                    equippedCardsData.error = "Error accessing slot array elements: " + e.message;
                    return equippedCardsData;
                }
                
                if (!slotCardInt_ElementsBase || !slotCardAssignedBool_ElementsBase) {
                    transmitData('script_status', 'equipped_cards_slot_bases_not_set_after_access_attempt', { preset: currentPreset });
                    equippedCardsData.error = "Slot element base pointers could not be determined.";
                    return equippedCardsData;
                }


                for (let slotIdx = 0; slotIdx < CONST_CARDS_SLOTS_PER_PRESET; slotIdx++) {
                    try {
                        // Calculate the flat index for the 2D array representation
                        const flatIndex = currentPreset * CONST_CARDS_SLOTS_PER_PRESET + slotIdx;

                        // Read isAssigned (boolean, 1 byte)
                        const assignedBoolOffset = flatIndex * 1; // Assuming System.Boolean[] elements are 1 byte each
                        const isAssigned = slotCardAssignedBool_ElementsBase.add(assignedBoolOffset).readU8();

                        if (isAssigned !== 0) { // Check if the slot is assigned (true)
                            // Read cardId (integer, 4 bytes)
                            const cardIdOffset = flatIndex * 4; // Assuming System.Int32[] elements are 4 bytes each
                            const cardId = slotCardInt_ElementsBase.add(cardIdOffset).readS32();

                            let name = "[Name N/A]";
                            let level = -1; // Default if not found

                            // Get card name
                            if (mainCardName_ElementsBasePtr && cardId >= 0 && cardId < mainCardName_ArrayLength) {
                                try {
                                    // Each element in mainCardName_ElementsBasePtr is a pointer to an Il2Cpp.String
                                    const stringPointer = mainCardName_ElementsBasePtr.add(cardId * Process.pointerSize).readPointer();
                                    if (!stringPointer.isNull()) {
                                        name = new Il2Cpp.String(stringPointer, Il2CppSystemString).content;
                                        if (name === null) name = "[NullContentStr]"; // Handle if content itself is null
                                    } else {
                                        name = "[NullNamePtr]";
                                    }
                                } catch (e_name) {
                                    name = "[NameReadError]";
                                    // transmitData('script_status', 'get_card_name_error', { cardId: cardId, slot: slotIdx, message: e_name.message });
                                }
                            } else if (!mainCardName_ElementsBasePtr) {
                                name = "[NameArrayUnavail]";
                            }


                            // Get card level
                            if (mainCardLevelArray && cardId >= 0 && cardId < mainCardLevelArray.length) {
                                try {
                                    level = mainCardLevelArray.get(cardId); // Direct access for Il2Cpp.Array of primitives
                                } catch (e_level) {
                                    level = -2; // Indicate level read error
                                    // transmitData('script_status', 'get_card_level_error', { cardId: cardId, slot: slotIdx, message: e_level.message });
                                }
                            } else if (!mainCardLevelArray) {
                                level = -3; // Indicate level array was not available
                            }

                            equippedCardsData.slots.push({
                                slot: slotIdx,
                                id: cardId,
                                name: name,
                                level: level
                            });
                        }
                    } catch (e_slot) {
                        // Log error for an individual slot but continue processing others
                        transmitData('script_status', 'get_equipped_cards_slot_error', { preset: currentPreset, slot: slotIdx, message: e_slot.message });
                        equippedCardsData.slots.push({
                            slot: slotIdx,
                            id: -1,
                            name: "[SlotReadError]",
                            level: -99,
                            error_message: e_slot.message
                        });
                    }
                } // end for loop

            } catch (e_main) {
                // Catch any broader errors in the function
                transmitData('script_status', 'get_equipped_cards_critical_outer_error', { message: e_main.message, stack: e_main.stack });
                equippedCardsData.error = "Critical error in getEquippedCards: " + e_main.message;
                // Return whatever data was collected, plus the error
            }

            return equippedCardsData;
        }

        // --- PERIODIC SNAPSHOT ---
        function logSnapshotData() {
            if (!mainInstance || mainInstance.isNull() || !isRoundActive) return;
            const snapshot = {
                cash: readField(mainInstance, OFFSET_MAIN_CASH, "double"),
                coins: readField(mainInstance, OFFSET_MAIN_COINS, "double"),
                gems: readField(mainInstance, OFFSET_MAIN_GEMS, "int"),
                current_wave: readField(mainInstance, OFFSET_MAIN_CURRENT_WAVE, "int"),
                // Add other fields like stones, cells, tower_health etc. from your original logPeriodicRoundData
                stones: readField(mainInstance, OFFSET_MAIN_STONES, "int"),
                cells: readField(mainInstance, OFFSET_MAIN_CELLS, "float"),
            };
            transmitData('round_snapshot_update', 'periodic_data', snapshot);
        }

        function stopSnapshotLogger() {
            if (snapshotIntervalId) {
                clearInterval(snapshotIntervalId);
                snapshotIntervalId = null;
                transmitData('script_status', 'snapshot_logger_stopped');
            }
        }

        function startSnapshotLogger() {
            stopSnapshotLogger();
            if (isRoundActive && mainInstance && !mainInstance.isNull()) {
                logSnapshotData(); // Log once immediately
                snapshotIntervalId = setInterval(logSnapshotData, INTERVAL_SNAPSHOT_LOGGING_MS);
                transmitData('script_status', 'snapshot_logger_started');
            }
        }

        // --- HOOKING HELPER for simple events ---
        function hookSimpleEvent(base, rva, fridaEventSubtype, onEnterCallback) {
            try {
                const address = base.add(rva);
                Interceptor.attach(address, {
                    onEnter: function(args) {
                        if (onEnterCallback) { // Allow custom logic if needed
                            onEnterCallback(args, this); // 'this' gives access to threadId, context, etc.
                        }
                        // Only send if round is active, or make it conditional
                        if (isRoundActive) { // Most in-round events only matter if round is active
                            transmitData('in_round_event', fridaEventSubtype, { args_count: args.length });
                        } else {
                             transmitData('script_status', fridaEventSubtype + '_outside_round', { args_count: args.length });
                        }
                    }
                    // onLeave: function(retval) { } // if needed
                });
                transmitData('script_status', 'hook_attached', { name: fridaEventSubtype });
            } catch (e) {
                transmitData('script_status', 'hook_attach_failed', { name: fridaEventSubtype, message: e.message });
                console.error(`[MasterHooker] Error attaching ${fridaEventSubtype}: ${e.message}`);
            }
        }


        // ===================================================================================
        // --- MAIN HOOKING LOGIC ---
        // ===================================================================================
        try {
            il2cppBase = Il2Cpp.module.base;
            if (!il2cppBase || il2cppBase.isNull()) {
                transmitData('script_status', 'critical_base_address_null');
                console.error("[MasterHooker] CRITICAL: il2cppBase is null. Aborting Il2Cpp.perform block.");
                return;
            }
            transmitData('script_status', 'base_address_acquired', { base: il2cppBase.toString() });
            initializeSystemTypes(); // Essential for reading strings, specific types

            // --- Hook Main.StartNewRound ---
            Interceptor.attach(il2cppBase.add(RVA_MAIN_START_NEW_ROUND), {
                onEnter: function(args) {
                    isRoundActive = true;
                    mainInstance = new Il2Cpp.Object(args[0]); // Capture 'this'
                    transmitData('script_status', 'main_instance_captured_startround', { handle: mainInstance.handle.toString()});

                    const tier = readField(mainInstance, OFFSET_MAIN_CURRENT_TIER, "int");
                    let initialCards = { error: "cardsInstance not available yet for StartNewRound" };
                    if (cardsInstance && !cardsInstance.isNull()) {
                        initialCards = getEquippedCards(cardsInstance);
                    } else {
                        // Attempt to get cardsInstance if CardPanelOpen hasn't fired yet
                        // This part is tricky, as Cards instance might not be the same as Main or easily accessible
                        // For now, we rely on CardPanelOpen to set cardsInstance.
                        // If cards are absolutely needed at StartNewRound and CardPanelOpen is too late,
                        // you'd need to find how to get the Cards instance from Main or globally.
                        transmitData('script_status', 'cards_instance_unavailable_at_startnewround');
                    }

                    transmitData('round_start_package', 'new_round_started', {
                        tier: tier,
                        cards: initialCards, // Will contain error if cardsInstance was null
                        // modules: {} // Placeholder for future
                    });
                    startSnapshotLogger();
                },
                onLeave: function(retval) {
                    // Optional: transmitData('in_round_event', 'start_new_round_leave');
                }
            });
            transmitData('script_status', 'hook_attached', { name: "StartNewRound" });


            // --- Hook Main.GameOver ---
            Interceptor.attach(il2cppBase.add(RVA_MAIN_GAME_OVER), {
                onEnter: function(args) {
                    if (!isRoundActive) { // GameOver can be called without a round being active from script's POV
                        transmitData('script_status', 'game_over_outside_active_round');
                        // Potentially still log some final data if mainInstance exists
                        if (mainInstance && !mainInstance.isNull()) {
                             const finalWave = readField(mainInstance, OFFSET_MAIN_CURRENT_WAVE, "int");
                             transmitData('round_end_package', 'round_ended_no_active_flag', {
                                final_wave: finalWave,
                                allow_death_saves: args[1].toInt32() !== 0,
                                cash: readField(mainInstance, OFFSET_MAIN_CASH, "double"),
                                coins: readField(mainInstance, OFFSET_MAIN_COINS, "double"),
                             });
                        }
                        isRoundActive = false; // Ensure it's false
                        stopSnapshotLogger();
                        return;
                    }

                    const allowDeathSaves = args[1].toInt32() !== 0; // Or readU8 if bool
                    stopSnapshotLogger(); // Stop before reading final values

                    let finalStats = { allow_death_saves: allowDeathSaves };
                    if (mainInstance && !mainInstance.isNull()) {
                        finalStats.final_wave = readField(mainInstance, OFFSET_MAIN_CURRENT_WAVE, "int");
                        finalStats.cash = readField(mainInstance, OFFSET_MAIN_CASH, "double");
                        finalStats.coins = readField(mainInstance, OFFSET_MAIN_COINS, "double");
                        finalStats.gems = readField(mainInstance, OFFSET_MAIN_GEMS, "int");
                        // ... other final stats from your original script (black_hole_coins, highest_wave_bool)
                    }
                    transmitData('round_end_package', 'round_ended', finalStats);
                    isRoundActive = false;
                }
            });
            transmitData('script_status', 'hook_attached', { name: "GameOver" });

            // --- Hook Cards.CardPanelOpen (to get cardsInstance) ---
            Interceptor.attach(il2cppBase.add(RVA_CARDS_CARD_PANEL_OPEN), {
                onEnter: function(args) {
                    cardsInstance = new Il2Cpp.Object(args[0]);
                    transmitData('script_status', 'cards_instance_captured', { handle: cardsInstance.handle.toString() });
                    
                    // When panel opens, log current cards. Python can decide if this is an "in_round_event"
                    // if isRoundActive is true, or just a general update.
                    const currentCards = getEquippedCards(cardsInstance);
                    transmitData('in_round_event', 'card_panel_opened_or_cards_updated', { 
                        cards_now: currentCards, 
                        round_active_status: isRoundActive 
                    });
                }
            });
            transmitData('script_status', 'hook_attached', { name: "CardPanelOpen" });

            // --- Hook Main.NewWave (as an in_round_event) ---
            Interceptor.attach(il2cppBase.add(ptr("0x13BB0B0" /*RVA_MAIN_NEW_WAVE*/)), {
                onLeave: function(retval) {
                    if (isRoundActive && mainInstance && !mainInstance.isNull()) {
                        const wave = readField(mainInstance, OFFSET_MAIN_CURRENT_WAVE, "int");
                        transmitData('in_round_event', 'new_wave_reached', { 
                            wave: wave,
                            // Optional: include base_damage, base_health etc. from your original script if needed for this event
                        });
                    }
                }
            });
             transmitData('script_status', 'hook_attached', { name: "NewWave" });


            // --- Setup for other simple event hooks using the helper ---
            const simpleEventDefinitions = [
                // { rva: RVA_MAIN_GEM_BLOCK_SPAWN, name: "gem_block_spawned" }, // ptr("0x13CF608")
                // { rva: RVA_MAIN_GEM_BLOCK_TAP, name: "gem_block_tapped" },     // ptr("0x13CF350")
                // ... Add other simple events from your original list, e.g.:
                // { rva: ptr("0x13D15A0"), name: "has_death_defied_checked"},
                // { rva: ptr("0x13C177C"), name: "is_boss_active_checked"},
                // { rva: ptr("0x13CFC48"), name: "game_paused"},
                // { rva: ptr("0x13CFCC0"), name: "game_unpaused"},
            ];

            simpleEventDefinitions.forEach(eventDef => {
                hookSimpleEvent(il2cppBase, eventDef.rva, eventDef.name);
            });

            // Fallback for mainInstance via Update (consider if truly needed now)
            // If StartNewRound reliably gives mainInstance, this adds overhead.
            // Could be useful if game starts mid-round observation.
            if (!mainInstance) { // Only try if not captured yet
                console.warn("[MasterHooker] Main instance not yet captured, will try Main.Update fallback.");
                // Your tryHookMainUpdate logic can be adapted here, sending script_status on capture
            }

            transmitData('script_status', 'all_hooks_configured');
            console.log("[MasterHooker] Il2Cpp.perform() All hooks configured.");

        } catch (e) {
            transmitData('script_status', 'critical_error_in_perform_block', { message: e.message, stack: e.stack });
            console.error(`[MasterHooker] CRITICAL Il2Cpp.perform block error: ${e.message}\nStack: ${e.stack}`);
        }
    }); // End of Il2Cpp.perform
}, 15000);

console.log("[MasterHooker] Script fully loaded. Il2Cpp.perform() was scheduled.");
transmitData('script_status', 'script_fully_loaded_perform_scheduled');