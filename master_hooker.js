// --- Frida Master Hooking Script for "The Tower" ---
import "frida-il2cpp-bridge";

console.log("Frida Master Hooking Script trying to load. Il2Cpp.perform() will be scheduled.");

// Delay Il2Cpp.perform to give the application more time to load libil2cpp.so
setTimeout(() => {
    console.log("[DELAYED START] Attempting Il2Cpp.perform() after 15 second delay.");
    Il2Cpp.perform(() => {
        // ===================================================================================
        // --- CONFIGURATION ---
        // ===================================================================================
        const CLASS_MAIN = "Main";
        const CLASS_CARDS = "Cards";

        const RVA_MAIN_START_NEW_ROUND = ptr("0x13B8BB4");
        const RVA_MAIN_GAME_OVER = ptr("0x13B74C8");
        const RVA_MAIN_GEM_BLOCK_SPAWN = ptr("0x13CF608");
        const RVA_MAIN_GEM_BLOCK_TAP = ptr("0x13CF350");
        const RVA_MAIN_HAS_DEATH_DEFIED = ptr("0x13D15A0");
        const RVA_MAIN_IS_BOSS_ACTIVE = ptr("0x13C177C");
        const RVA_MAIN_NEW_WAVE = ptr("0x13BB0B0");
        const RVA_MAIN_PAUSE = ptr("0x13CFC48");
        const RVA_MAIN_UNPAUSE = ptr("0x13CFCC0");
        const RVA_MAIN_RESUME_ROUND = ptr("0x13D048C");
        const RVA_MAIN_RESUME_ROUND_CANCEL = ptr("0x13D0510");
        const RVA_MAIN_SPAWN_RECOVERY_PACKAGE = ptr("0x13C0D40");
        const RVA_MAIN_UPDATE = ptr("0x13B69B0");
        const RVA_MAIN_WALL_DESTROYED = ptr("0x13CDCBC");

        const OFFSET_MAIN_BLACK_HOLE_COINS = ptr("0x748");
        const OFFSET_MAIN_CASH = ptr("0x108");
        const OFFSET_MAIN_CELLS = ptr("0x124");
        const OFFSET_MAIN_COINS = ptr("0x110");
        const OFFSET_MAIN_COINS_BONUS_TOTAL = ptr("0x12C");
        const OFFSET_MAIN_CURRENT_TIER = ptr("0x5A8");
        const OFFSET_MAIN_CURRENT_WAVE = ptr("0x148");
        const OFFSET_MAIN_CURRENT_WAVE_BASE_DAMAGE = ptr("0x138");
        const OFFSET_MAIN_CURRENT_WAVE_BASE_HEALTH = ptr("0x130");
        const OFFSET_MAIN_CURRENT_WAVE_BASE_KILL_CASH = ptr("0x140");
        const OFFSET_MAIN_CURRENT_WAVE_KILL_COINS = ptr("0x170");
        const OFFSET_MAIN_FRAMERATE = ptr("0x258");
        const OFFSET_MAIN_GEMS = ptr("0x118");
        const OFFSET_MAIN_HIGHEST_WAVE_BOOL = ptr("0x5B8");
        const OFFSET_MAIN_STONES = ptr("0x11C");
        const OFFSET_MAIN_TIER_COIN_MULTIPLIER = ptr("0x5B0");
        const OFFSET_MAIN_TIER_DIFFICULTY_MULTIPLIER = ptr("0x5B4");
        const OFFSET_MAIN_TOWER_HEALTH = ptr("0x388");
        const OFFSET_MAIN_TOWER_MAX_HEALTH = ptr("0x390");

        const RVA_CARDS_CARD_PANEL_OPEN = ptr("0x21AE684");
        const OFFSET_CARDS_LEVEL_ARRAY = ptr("0x48");
        const OFFSET_CARDS_NAME_ARRAY = ptr("0x68");
        const OFFSET_CARDS_CURRENT_PRESET = ptr("0x120");
        const OFFSET_CARDS_SLOT_CARD_INT_2D = ptr("0x110");
        const OFFSET_CARDS_SLOT_CARD_ASSIGNED_BOOL_2D = ptr("0x108");
        const CONST_CARDS_SLOTS_PER_PRESET = 27;
        const CONST_ARRAY_ELEMENTS_START_OFFSET = (Process.arch === "arm64" || Process.arch === "x64") ? 0x20 : 0x10;

        const INTERVAL_RESOURCE_LOGGING_MS = 1000;

        let Il2CppSystemBoolean, Il2CppSystemInt32, Il2CppSystemString, Il2CppSystemDouble, Il2CppSystemSingle, Il2CppSystemVoid;
        let Il2CppSystemByte, Il2CppSystemSByte, Il2CppSystemInt16, Il2CppSystemUInt16, Il2CppSystemInt64, Il2CppSystemUInt64;

        // ===================================================================================
        // --- GLOBAL STATE & INSTANCE STORAGE ---
        // ===================================================================================
        let il2cppBase = ptr(0);
        let mainInstance = null;
        let cardsInstance = null;
        let isRoundActive = false;
        let roundStartTime = 0;
        let currentRoundData = {};
        let resourceLoggerIntervalId = null;
        let initialRoundStateCaptured = false;
        let mainUpdateHookListener = null;

        // ===================================================================================
        // --- UTILITY FUNCTIONS ---
        // ===================================================================================
        function sendGameData(type, event, data = {}) {
            try {
                const payload = { event: event, data: data };
                send({ frida_type: 'game_data', type: type, timestamp: new Date().toISOString(), payload: payload });
            } catch (e) { /* console.error("Error sending game data: " + e); */ }
        }

        function readField(instance, offset, type) {
            if (!instance || instance.isNull()) { return null; }
            try {
                const fieldPtr = instance.handle.add(offset);
                switch (type) {
                    case "bool": return fieldPtr.readU8() !== 0;
                    case "byte": return fieldPtr.readU8();
                    case "sbyte": return fieldPtr.readS8();
                    case "short": return fieldPtr.readS16();
                    case "ushort": return fieldPtr.readU16();
                    case "int": return fieldPtr.readS32();
                    case "uint": return fieldPtr.readU32();
                    case "long": return fieldPtr.readS64();
                    case "ulong": return fieldPtr.readU64();
                    case "float": return fieldPtr.readFloat();
                    case "double": return fieldPtr.readDouble();
                    case "string":
                        const strPtr = fieldPtr.readPointer();
                        if (strPtr.isNull()) return null;
                        if (!Il2CppSystemString) { console.warn("readField: Il2CppSystemString not initialized."); return "[SysStrN/A]"; }
                        return new Il2Cpp.String(strPtr, Il2CppSystemString).content;
                    case "pointer": return fieldPtr.readPointer();
                    default: sendGameData('warning', 'read_field_unknown_type', { type: type, offset: offset.toString() }); return null;
                }
            } catch (e) { sendGameData('error', 'read_field_exception', { offset: offset.toString(), type: type, message: e.message }); return null; }
        }

        // ===================================================================================
        // --- CORE LOGIC FUNCTIONS ---
        // ===================================================================================
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
                sendGameData('status', 'system_types_initialized_successfully');
                console.log("Il2Cpp.perform: System types initialized.");
            } catch (e) {
                sendGameData('error', 'system_types_init_failed', { message: e.message });
                console.error("Il2Cpp.perform: ERROR in initializeSystemTypes: " + e.message);
            }
        }

        function logEquippedCards(cardsInst) {
            // This is the full, working version from the previous successful output
            if (!cardsInst || (cardsInst.isNull && typeof cardsInst.isNull === 'function' && cardsInst.isNull())) { return; }
            if (!Il2CppSystemInt32 || !Il2CppSystemString || !SystemBooleanClass ) { // Corrected to SystemBooleanClass
                sendGameData('warning', 'log_equipped_cards_system_types_not_ready');
                return;
            }

            let mainCardLevelArray = null;
            let mainCardName_ElementsBasePtr = null;
            let mainCardName_ArrayLength = 0;
            let equippedCardsData = { preset: -1, slots: [] };

            try {
                const mainCardLevelPtr = cardsInst.handle.add(OFFSET_CARDS_LEVEL_ARRAY).readPointer();
                const mainCardNamePtr = cardsInst.handle.add(OFFSET_CARDS_NAME_ARRAY).readPointer();

                if (!mainCardLevelPtr.isNull()) {
                    try { mainCardLevelArray = new Il2Cpp.Array(mainCardLevelPtr, Il2CppSystemInt32); }
                    catch (e) { /* mainCardLevelArray will remain null */ }
                }
                if (!mainCardNamePtr.isNull()) {
                    try {
                        const tempNameArrayWrapper = new Il2Cpp.Array(mainCardNamePtr);
                        mainCardName_ArrayLength = tempNameArrayWrapper.length;
                        if (tempNameArrayWrapper.elements && typeof tempNameArrayWrapper.elements.add === 'function') {
                            mainCardName_ElementsBasePtr = tempNameArrayWrapper.elements;
                        } else {
                            mainCardName_ElementsBasePtr = mainCardNamePtr.add(CONST_ARRAY_ELEMENTS_START_OFFSET);
                        }
                    } catch (e) { /* mainCardName_ElementsBasePtr will remain null */ }
                }

                const currentPreset = cardsInst.handle.add(OFFSET_CARDS_CURRENT_PRESET).readInt();
                equippedCardsData.preset = currentPreset;

                const slotCardInt_ArrayPtr = cardsInst.handle.add(OFFSET_CARDS_SLOT_CARD_INT_2D).readPointer();
                const slotCardAssignedBool_ArrayPtr = cardsInst.handle.add(OFFSET_CARDS_SLOT_CARD_ASSIGNED_BOOL_2D).readPointer();

                if (slotCardInt_ArrayPtr.isNull() || slotCardAssignedBool_ArrayPtr.isNull()) {
                    sendGameData('warning', 'equipped_cards_slot_arrays_null', { preset: currentPreset });
                    return;
                }

                let slotCardInt_ElementsBase = null;
                let slotCardAssignedBool_ElementsBase = null;
                try {
                    const tempIntWrapper = new Il2Cpp.Array(slotCardInt_ArrayPtr);
                    if (tempIntWrapper.elements && typeof tempIntWrapper.elements.add === 'function') {
                        slotCardInt_ElementsBase = tempIntWrapper.elements;
                    } else {
                        slotCardInt_ElementsBase = slotCardInt_ArrayPtr.add(CONST_ARRAY_ELEMENTS_START_OFFSET);
                    }
                    const tempBoolWrapper = new Il2Cpp.Array(slotCardAssignedBool_ArrayPtr);
                    if (tempBoolWrapper.elements && typeof tempBoolWrapper.elements.add === 'function') {
                        slotCardAssignedBool_ElementsBase = tempBoolWrapper.elements;
                    } else {
                        slotCardAssignedBool_ElementsBase = slotCardAssignedBool_ArrayPtr.add(CONST_ARRAY_ELEMENTS_START_OFFSET);
                    }
                } catch (e) { sendGameData('error', 'equipped_cards_slot_bases_error', { message: e.message }); return; }

                if (!slotCardInt_ElementsBase || !slotCardAssignedBool_ElementsBase) {
                     sendGameData('warning', 'equipped_cards_slot_bases_not_set', { preset: currentPreset });
                    return;
                }

                for (let slotIdx = 0; slotIdx < CONST_CARDS_SLOTS_PER_PRESET; slotIdx++) {
                    try {
                        const assignedBoolOffset = (currentPreset * CONST_CARDS_SLOTS_PER_PRESET + slotIdx) * 1; // bool is 1 byte
                        const isAssigned = slotCardAssignedBool_ElementsBase.add(assignedBoolOffset).readU8();

                        if (isAssigned) {
                            const cardIdOffset = (currentPreset * CONST_CARDS_SLOTS_PER_PRESET + slotIdx) * 4; // int is 4 bytes
                            const cardId = slotCardInt_ElementsBase.add(cardIdOffset).readInt();
                            let name = "[N/A]"; let level = -1;

                            if (mainCardName_ElementsBasePtr && cardId >= 0 && cardId < mainCardName_ArrayLength) {
                                try {
                                    const rawPtr = mainCardName_ElementsBasePtr.add(cardId * Process.pointerSize).readPointer();
                                    if (!rawPtr.isNull() && rawPtr.compare(0x10000) >= 0) { // Heuristic for valid pointer
                                        name = new Il2Cpp.String(rawPtr, Il2CppSystemString).content || "[NC]";
                                    } else { name = rawPtr.isNull() ? "[NullNPtr]" : `[SmPtr:${rawPtr}]`;}
                                } catch (e) { name = "[NErr]"; }
                            }
                            if (mainCardLevelArray && cardId >= 0 && cardId < mainCardLevelArray.length) {
                                try { level = mainCardLevelArray.get(cardId); } catch (e) { level = -2; }
                            }
                            equippedCardsData.slots.push({ slot: slotIdx, id: cardId, name: name, level: level });
                        }
                    } catch (e_slot) { /* ignore individual slot read errors, but ideally log them if persistent */ }
                }
            } catch (e) { sendGameData('error', 'log_equipped_cards_critical', { message: e.message }); }

            // Send data even if slots array is empty, to indicate no cards are equipped in the preset
            sendGameData('data', 'equipped_cards_update', equippedCardsData);
        }

        function captureInitialRoundState() {
            if (!mainInstance || mainInstance.isNull() || initialRoundStateCaptured) return;
            sendGameData('status', 'capturing_initial_round_state');

            const initialState = {
                timestamp: new Date().toISOString(),
                tier: readField(mainInstance, OFFSET_MAIN_CURRENT_TIER, "int"),
                tier_coin_multiplier: readField(mainInstance, OFFSET_MAIN_TIER_COIN_MULTIPLIER, "float"),
                tier_difficulty_multiplier: readField(mainInstance, OFFSET_MAIN_TIER_DIFFICULTY_MULTIPLIER, "float"),
                initial_wave: readField(mainInstance, OFFSET_MAIN_CURRENT_WAVE, "int"),
                workshop_levels: null, // Placeholder for actual workshop data
                // equipped_cards field will be populated by a separate 'equipped_cards_update' message
            };

            // Log equipped cards at round start
            if (cardsInstance && !cardsInstance.isNull()) {
                logEquippedCards(cardsInstance);
            } else {
                sendGameData('warning', 'initial_round_state_no_cards_instance');
            }

            // Placeholder: Log workshop levels (user needs to implement get workshopInstance and logWorkshopLevels)
            // if (workshopInstance && !workshopInstance.isNull()) {
            //     initialState.workshop_levels = logWorkshopLevels(workshopInstance);
            // } else {
            //     sendGameData('warning', 'initial_round_state_no_workshop_instance');
            // }

            currentRoundData.initial_state = initialState;
            sendGameData('round_event', 'round_start_data_captured', initialState);
            initialRoundStateCaptured = true;
        }

        function logPeriodicRoundData() {
            if (!mainInstance || mainInstance.isNull() || !isRoundActive) return;

            const data = {
                cash: readField(mainInstance, OFFSET_MAIN_CASH, "double"),
                coins: readField(mainInstance, OFFSET_MAIN_COINS, "double"),
                gems: readField(mainInstance, OFFSET_MAIN_GEMS, "int"),
                cells: readField(mainInstance, OFFSET_MAIN_CELLS, "float"), // Added from your list
                stones: readField(mainInstance, OFFSET_MAIN_STONES, "int"), // Added
                current_wave: readField(mainInstance, OFFSET_MAIN_CURRENT_WAVE, "int"),
                tower_health: readField(mainInstance, OFFSET_MAIN_TOWER_HEALTH, "double"),
                tower_max_health: readField(mainInstance, OFFSET_MAIN_TOWER_MAX_HEALTH, "double"),
                framerate: readField(mainInstance, OFFSET_MAIN_FRAMERATE, "int"), // Added
                coins_bonus_total: readField(mainInstance, OFFSET_MAIN_COINS_BONUS_TOTAL, "float"), // Added
                // Wave specific data - might be better to log these on NewWave.onLeave
                // current_wave_base_damage: readField(mainInstance, OFFSET_MAIN_CURRENT_WAVE_BASE_DAMAGE, "double"),
                // current_wave_base_health: readField(mainInstance, OFFSET_MAIN_CURRENT_WAVE_BASE_HEALTH, "double"),
                // current_wave_base_kill_cash: readField(mainInstance, OFFSET_MAIN_CURRENT_WAVE_BASE_KILL_CASH, "double"),
                // current_wave_kill_coins: readField(mainInstance, OFFSET_MAIN_CURRENT_WAVE_KILL_COINS, "double"),
            };
            currentRoundData.periodic_updates = currentRoundData.periodic_updates || [];
            currentRoundData.periodic_updates.push({timestamp: new Date().toISOString(), data: data});
            sendGameData('data', 'round_update', data);
        }

        function stopResourceLogger() {
            if (resourceLoggerIntervalId) {
                clearInterval(resourceLoggerIntervalId);
                resourceLoggerIntervalId = null;
                sendGameData('status', 'resource_logger_stopped');
            }
        }

        function startResourceLogger() {
            stopResourceLogger(); 
            if (isRoundActive && mainInstance && !mainInstance.isNull()) {
                // Call once immediately, then set interval
                logPeriodicRoundData();
                resourceLoggerIntervalId = setInterval(logPeriodicRoundData, INTERVAL_RESOURCE_LOGGING_MS);
                sendGameData('status', 'resource_logger_started');
            }
        }


        // ===================================================================================
        // --- MAIN Il2Cpp.perform BLOCK ---
        // ===================================================================================
        console.log("!!! INSIDE Il2Cpp.perform() - Callback Has Started Execution !!!");
        send({ frida_type: 'game_data', type: 'debug_perform', timestamp: new Date().toISOString(), payload: {event: 'EXEC_PERFORM_CALLBACK_STARTED', data: {}} });

        try {
            console.log("Il2Cpp.perform: Attempting to get Il2Cpp.module.base...");
            il2cppBase = Il2Cpp.module.base;
            console.log("Il2Cpp.perform: Il2Cpp.module.base = " + il2cppBase);
            send({ frida_type: 'game_data', type: 'debug_perform', timestamp: new Date().toISOString(), payload: {event: 'BASE_ACQUIRED', data: { base: il2cppBase ? il2cppBase.toString() : "null" }} });

            if (!il2cppBase || il2cppBase.isNull()) {
                console.error("Il2Cpp.perform: CRITICAL - il2cppBase is null. Aborting.");
                send({frida_type: 'game_data', type: 'critical_error', timestamp: new Date().toISOString(), payload: {event: 'il2cppBase_is_null_in_perform', data: {}}});
                return;
            }

            initializeSystemTypes();

            // --- Hook Main.StartNewRound ---
            try {
                console.log("Il2Cpp.perform: Setting up Main.StartNewRound hook...");
                const mainStartNewRoundAddr = il2cppBase.add(RVA_MAIN_START_NEW_ROUND);
                Interceptor.attach(mainStartNewRoundAddr, {
                    onEnter: function(args) {
                        console.log("Main.StartNewRound ENTERED");
                        sendGameData('game_event', 'start_new_round_onenter');
                        isRoundActive = true; initialRoundStateCaptured = false; roundStartTime = Date.now();
                        currentRoundData = { start_time_iso: new Date(roundStartTime).toISOString(), initial_state: {}, events: [], periodic_updates: [], final_state: {} };
                        if (!mainInstance || mainInstance.isNull()) {
                             const instancePtr = args[0];
                             if(instancePtr && !instancePtr.isNull()){ mainInstance = new Il2Cpp.Object(instancePtr); sendGameData('status', 'main_instance_captured', { handle: mainInstance.handle.toString() }); }
                             else { sendGameData('error', 'main_instance_ptr_null_in_startnewround'); }
                        }
                    },
                    onLeave: function(retval) {
                        console.log("Main.StartNewRound EXITED");
                        sendGameData('game_event', 'start_new_round_onleave');
                        if (mainInstance && !mainInstance.isNull()) { captureInitialRoundState(); startResourceLogger(); }
                        else { sendGameData('warning', 'main_instance_still_null_on_start_new_round_leave'); }
                    }
                });
                sendGameData('status', 'hook_main_start_new_round_attached');
                console.log("Il2Cpp.perform: Main.StartNewRound hook ATTACHED.");
            } catch (e) {
                console.error("Il2Cpp.perform: ERROR attaching Main.StartNewRound: " + e.message);
                sendGameData('error', 'hook_attach_failed_start_new_round', { message: e.message });
            }

            // --- Hook Cards.CardPanelOpen ---
            try {
                console.log("Il2Cpp.perform: Setting up Cards.CardPanelOpen hook...");
                const cardsPanelOpenAddr = il2cppBase.add(RVA_CARDS_CARD_PANEL_OPEN);
                Interceptor.attach(cardsPanelOpenAddr, {
                    onEnter: function(args) {
                        console.log("Cards.CardPanelOpen ENTERED");
                        const instancePtr = args[0];
                        if (instancePtr && !instancePtr.isNull()) {
                            cardsInstance = new Il2Cpp.Object(instancePtr);
                            sendGameData('status', 'cards_instance_captured', { handle: cardsInstance.handle.toString() });
                            logEquippedCards(cardsInstance);
                        }
                    }
                });
                sendGameData('status', 'hook_cards_panel_open_attached');
                console.log("Il2Cpp.perform: Cards.CardPanelOpen hook ATTACHED.");
            } catch (e) {
                console.error("Il2Cpp.perform: ERROR attaching Cards.CardPanelOpen: " + e.message);
                sendGameData('error', 'hook_attach_failed_cards_panel_open', { message: e.message });
            }

            // --- Hook Main.GameOver ---
            try {
                console.log("Il2Cpp.perform: Setting up Main.GameOver hook...");
                const mainGameOverAddr = il2cppBase.add(RVA_MAIN_GAME_OVER);
                Interceptor.attach(mainGameOverAddr, {
                    onEnter: function(args) {
                        console.log("Main.GameOver ENTERED");
                        const allowDeathSaves = args[1].toInt32() !== 0;
                        sendGameData('game_event', 'game_over_onenter', { allow_death_saves: allowDeathSaves });
                        const prevRoundActiveState = isRoundActive; isRoundActive = false; initialRoundStateCaptured = false;
                        stopResourceLogger();
                        if (mainInstance && !mainInstance.isNull()) {
                            currentRoundData.final_state = {
                                timestamp: new Date().toISOString(),
                                black_hole_coins: readField(mainInstance, OFFSET_MAIN_BLACK_HOLE_COINS, "double"),
                                highest_wave_bool: readField(mainInstance, OFFSET_MAIN_HIGHEST_WAVE_BOOL, "bool"),
                                final_wave: readField(mainInstance, OFFSET_MAIN_CURRENT_WAVE, "int"),
                            };
                        }
                        currentRoundData.end_time_iso = new Date().toISOString();
                        if (currentRoundData.start_time_iso) { currentRoundData.duration_ms = new Date(currentRoundData.end_time_iso).getTime() - new Date(currentRoundData.start_time_iso).getTime(); }
                        else { currentRoundData.duration_ms = 0; }
                        if (prevRoundActiveState) { sendGameData('round_event', 'round_over_final_data', currentRoundData); }
                        else { sendGameData('status', 'game_over_outside_active_round', currentRoundData.final_state); }
                        currentRoundData = {};
                    }
                });
                sendGameData('status', 'hook_main_game_over_attached');
                console.log("Il2Cpp.perform: Main.GameOver hook ATTACHED.");
            } catch (e) {
                console.error("Il2Cpp.perform: ERROR attaching Main.GameOver: " + e.message);
                sendGameData('error', 'hook_attach_failed_game_over', { message: e.message });
            }

            // --- Hook Main.NewWave ---
            try {
                console.log("Il2Cpp.perform: Setting up Main.NewWave hook...");
                const mainNewWaveAddr = il2cppBase.add(RVA_MAIN_NEW_WAVE);
                Interceptor.attach(mainNewWaveAddr, {
                    onLeave: function(retval) { // Data is updated after NewWave logic runs
                        if (mainInstance && !mainInstance.isNull() && isRoundActive) {
                            const wave = readField(mainInstance, OFFSET_MAIN_CURRENT_WAVE, "int");
                            console.log("Main.NewWave EXITED - Current Wave: " + wave);
                            sendGameData('game_event', 'new_wave_updated', { wave: wave });
                            // Log wave-specific income/damage values if desired
                            const waveData = {
                                wave: wave,
                                base_damage: readField(mainInstance, OFFSET_MAIN_CURRENT_WAVE_BASE_DAMAGE, "double"),
                                base_health: readField(mainInstance, OFFSET_MAIN_CURRENT_WAVE_BASE_HEALTH, "double"),
                                kill_cash: readField(mainInstance, OFFSET_MAIN_CURRENT_WAVE_BASE_KILL_CASH, "double"),
                                kill_coins: readField(mainInstance, OFFSET_MAIN_CURRENT_WAVE_KILL_COINS, "double")
                            };
                            sendGameData('data', 'wave_stats_update', waveData);
                        }
                    }
                });
                sendGameData('status', 'hook_main_new_wave_attached');
                console.log("Il2Cpp.perform: Main.NewWave hook ATTACHED.");
            } catch (e) {
                console.error("Il2Cpp.perform: ERROR attaching Main.NewWave: " + e.message);
                sendGameData('error', 'hook_attach_failed_new_wave', { message: e.message });
            }

            // --- Hook Main.GemBlockTap ---
            try {
                console.log("Il2Cpp.perform: Setting up Main.GemBlockTap hook...");
                const mainGemBlockTapAddr = il2cppBase.add(RVA_MAIN_GEM_BLOCK_TAP);
                Interceptor.attach(mainGemBlockTapAddr, {
                    onEnter: function(args) {
                        console.log("Main.GemBlockTap ENTERED");
                        sendGameData('game_event', 'gem_block_tapped');
                    }
                });
                sendGameData('status', 'hook_main_gem_block_tap_attached');
                console.log("Il2Cpp.perform: Main.GemBlockTap hook ATTACHED.");
            } catch (e) {
                console.error("Il2Cpp.perform: ERROR attaching Main.GemBlockTap: " + e.message);
                sendGameData('error', 'hook_attach_failed_gem_block_tap', { message: e.message });
            }
            
            // --- Add other simple event hooks from your list ---
            const simpleEventHooks = [
                { name: "GemBlockSpawn", rva: RVA_MAIN_GEM_BLOCK_SPAWN, eventName: "gem_block_spawned" },
                { name: "HasDeathDefied", rva: RVA_MAIN_HAS_DEATH_DEFIED, eventName: "has_death_defied_checked" }, // Could log retval
                { name: "IsBossActive", rva: RVA_MAIN_IS_BOSS_ACTIVE, eventName: "is_boss_active_checked" },     // Could log retval
                { name: "Pause", rva: RVA_MAIN_PAUSE, eventName: "game_paused" },
                { name: "Unpause", rva: RVA_MAIN_UNPAUSE, eventName: "game_unpaused" },
                { name: "ResumeRound", rva: RVA_MAIN_RESUME_ROUND, eventName: "round_resumed" },
                { name: "ResumeRoundCancel", rva: RVA_MAIN_RESUME_ROUND_CANCEL, eventName: "round_resume_cancelled" },
                { name: "SpawnRecoveryPackage", rva: RVA_MAIN_SPAWN_RECOVERY_PACKAGE, eventName: "recovery_package_spawned" },
                { name: "WallDestroyed", rva: RVA_MAIN_WALL_DESTROYED, eventName: "wall_destroyed" },
            ];

            simpleEventHooks.forEach(hookInfo => {
                try {
                    console.log(`Il2Cpp.perform: Setting up Main.${hookInfo.name} hook...`);
                    const addr = il2cppBase.add(hookInfo.rva);
                    Interceptor.attach(addr, {
                        onEnter: function(args) { // Or onLeave if more appropriate
                            console.log(`Main.${hookInfo.name} ENTERED`);
                            sendGameData('game_event', hookInfo.eventName);
                        }
                    });
                    sendGameData('status', `hook_main_${hookInfo.name}_attached`);
                    console.log(`Il2Cpp.perform: Main.${hookInfo.name} hook ATTACHED.`);
                } catch (e) {
                    console.error(`Il2Cpp.perform: ERROR attaching Main.${hookInfo.name}: ${e.message}`);
                    sendGameData('error', `hook_attach_failed_${hookInfo.name}`, { message: e.message });
                }
            });


            // --- Hook Main.Update (Fallback for mainInstance) ---
            let updateHookAttempted = false;
            function tryHookMainUpdate() {
                if (updateHookAttempted) return;
                updateHookAttempted = true;
                if (mainInstance && !mainInstance.isNull()) return;

                console.log("Il2Cpp.perform: Attempting to hook Main.Update as fallback...");
                try {
                    const mainUpdateAddr = il2cppBase.add(RVA_MAIN_UPDATE);
                    mainUpdateHookListener = Interceptor.attach(mainUpdateAddr, {
                        onEnter: function (args) {
                            if (!mainInstance || mainInstance.isNull()) {
                                const instancePtr = args[0];
                                if (instancePtr && !instancePtr.isNull()) {
                                    mainInstance = new Il2Cpp.Object(instancePtr);
                                    sendGameData('status', 'main_instance_captured_via_update', { handle: mainInstance.handle.toString() });
                                    console.log("Main.Update Hook: mainInstance captured: " + mainInstance.handle);
                                    if (mainUpdateHookListener) {
                                        mainUpdateHookListener.detach();
                                        mainUpdateHookListener = null;
                                        sendGameData('status', 'main_update_hook_detached_after_capture');
                                        console.log("Main.Update Hook: Detached self after capture.");
                                    }
                                }
                            } else {
                                if (mainUpdateHookListener) {
                                    mainUpdateHookListener.detach();
                                    mainUpdateHookListener = null;
                                    console.log("Main.Update Hook: Detached self as mainInstance already exists.");
                                }
                            }
                        }
                    });
                    sendGameData('status', 'hook_main_update_attached_for_instance_capture');
                    console.log("Il2Cpp.perform: Main.Update hook ATTACHED.");
                } catch(e) {
                    sendGameData('error', 'hook_main_update_failed', {message: e.message});
                    console.error("Il2Cpp.perform: Error attaching Main.Update hook: " + e.message);
                }
            }
            setTimeout(tryHookMainUpdate, 3000);


            sendGameData('status', 'all_hooks_configured_waiting_for_triggers');
            console.log("!!! Il2Cpp.perform() - ALL INITIAL HOOKS AND SETUP COMPLETED !!!");

        } catch (e) {
            console.error("!!!! CRITICAL ERROR INSIDE Il2Cpp.perform !!!!: " + e.message + "\nStack: " + e.stack);
            send({frida_type: 'game_data', type: 'critical_error', timestamp: new Date().toISOString(), payload: {event: "Error in Il2Cpp.perform main block: " + e.message, data: {stack: e.stack}}});
        }
        console.log("!!! Il2Cpp.perform() - Callback Execution FINISHED (or errored out) !!!");
    }); // End of Il2Cpp.perform
}, 15000); // 15-second delay

console.log("Frida Master Hooking Script - End of global script, Il2Cpp.perform() has been scheduled with a delay.");
// --- END OF SCRIPT ---
// Make sure no top-level Il2Cpp.perform() calls exist outside the setTimeout now.