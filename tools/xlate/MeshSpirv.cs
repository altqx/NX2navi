// Turns the compute SPIR-V the translator made from a lowered NVN2 task or mesh program (AmpereLowering.MeshInfo) into a
// TaskEXT or MeshEXT module (SPV_EXT_mesh_shader, VK_EXT_mesh_shader).
//
// The translator's main function is kept as it is and called from a new entry function:
//   mesh: [after a task stage: the task payload copied into the shared memory payload area, barrier] main; barrier;
//         SetMeshOutputsEXT(vertex limit, primitive count from shared memory); every thread copies vertices
//         i, i + threads, ... and primitives likewise from the shared memory stage buffer into the outputs: position
//         and point size built-ins, generic vector g at location g, the primitive's vertex indices.
//   task: main; barrier; the payload area copied out to the TaskPayloadWorkgroupEXT variable; EmitMeshTasksEXT with
//         the mesh workgroup count the program wrote.
// An EXIT in main returns to the entry function, so threads that leave early still reach the barrier. SPV_EXT_mesh_shader
// needs SPIR-V 1.4, where BufferBlock is gone: the translator's storage buffers (Uniform + BufferBlock, SPIR-V 1.3) move
// to the StorageBuffer storage class with Block, and the entry point lists every global variable.
//
// Opcode and enum numbers are the SPIR-V 1.6 specification's and SPV_EXT_mesh_shader's.
using System;
using System.Collections.Generic;
using System.Linq;

static class MeshSpirv
{
    // Generic vectors the mesh program writes (output locations).
    public static IEnumerable<int> GenericLocations(AmpereLowering.MeshInfo m)
    {
        var seen = new SortedSet<int>();
        foreach (int a in m.Lines) if (a >= 0x80) seen.Add((a - 0x80) / 16);
        return seen;
    }

    const int OpExtension = 10, OpExtInstImport = 11, OpExtInst = 12, OpMemoryModel = 14, OpEntryPoint = 15,
        OpExecutionMode = 16, OpCapability = 17, OpTypeVoid = 19, OpTypeBool = 20, OpTypeInt = 21, OpTypeFloat = 22,
        OpTypeVector = 23, OpTypeArray = 28, OpTypePointer = 32, OpTypeFunction = 33, OpConstant = 43,
        OpFunction = 54, OpFunctionEnd = 56, OpFunctionCall = 57, OpVariable = 59, OpLoad = 61, OpStore = 62,
        OpAccessChain = 65, OpInBoundsAccessChain = 66, OpPtrAccessChain = 67, OpInBoundsPtrAccessChain = 70,
        OpDecorate = 71, OpCompositeConstruct = 80, OpIAdd = 128, OpIMul = 132, OpULessThan = 176,
        OpShiftRightLogical = 194, OpBitwiseAnd = 199, OpBitcast = 124, OpControlBarrier = 224, OpSelectionMerge = 247,
        OpLabel = 248, OpBranch = 249, OpBranchConditional = 250, OpReturn = 253,
        OpEmitMeshTasksEXT = 5294, OpSetMeshOutputsEXT = 5295;
    const int ScUniform = 2, ScOutput = 3, ScWorkgroup = 4, ScInput = 1, ScFunction = 7, ScStorageBuffer = 12, ScTaskPayload = 5402;
    const int DecBlock = 2, DecBufferBlock = 3, DecBuiltIn = 11, DecLocation = 30;
    const int BiPosition = 0, BiPointSize = 1, BiLocalInvocationIndex = 29,
        BiPrimitivePointIndices = 5294, BiPrimitiveLineIndices = 5295, BiPrimitiveTriangleIndices = 5296;
    const int CapMeshShading = 5283, ModelTask = 5364, ModelMesh = 5365, ModelGLCompute = 5;
    const int ModeOutputVertices = 26, ModeOutputPoints = 27, ModeOutputLines = 5269, ModeOutputPrimitives = 5270,
        ModeOutputTriangles = 5298;
    const int GlslUMin = 38;

    sealed class Insn
    {
        public int Op;
        public List<uint> W;   // operands, without the opcode word
        public Insn(int op, IEnumerable<uint> w) { Op = op; W = w.ToList(); }
    }

    static List<uint> Str(string s)
    {
        var bytes = System.Text.Encoding.UTF8.GetBytes(s + "\0");
        var w = new List<uint>();
        for (int i = 0; i < bytes.Length; i += 4)
        {
            uint v = 0;
            for (int k = 0; k < 4 && i + k < bytes.Length; k++) v |= (uint)bytes[i + k] << (8 * k);
            w.Add(v);
        }
        return w;
    }

    // The first function that reaches itself through OpFunctionCall, or -1. Vulkan SPIR-V forbids recursion
    // (VUID-StandaloneSpirv-None-04634); NVIDIA's compiler inlines such a cycle until its thread's stack overflows.
    public static long CallCycle(byte[] spirv)
    {
        var w = new uint[spirv.Length / 4];
        Buffer.BlockCopy(spirv, 0, w, 0, w.Length * 4);
        var calls = new Dictionary<uint, List<uint>>();
        uint fn = 0;
        for (int p = 5; p < w.Length;)
        {
            int n = (int)(w[p] >> 16), op = (int)(w[p] & 0xffff);
            if (n == 0) break;
            if (op == OpFunction) fn = w[p + 2];
            else if (op == OpFunctionCall) { if (!calls.TryGetValue(fn, out var l)) calls[fn] = l = new(); l.Add(w[p + 3]); }
            p += n;
        }
        var state = new Dictionary<uint, int>();   // 1 on the path, 2 done
        long found = -1;
        bool Visit(uint f)
        {
            state[f] = 1;
            if (calls.TryGetValue(f, out var l))
                foreach (uint g in l)
                {
                    if (state.TryGetValue(g, out int st) ? st == 1 : Visit(g)) { if (found < 0) found = g; return true; }
                }
            state[f] = 2;
            return false;
        }
        foreach (uint f in calls.Keys.ToList()) if (!state.ContainsKey(f) && Visit(f)) break;
        return found;
    }

    public static byte[] Convert(byte[] spirv, AmpereLowering.MeshInfo m)
    {
        if (spirv.Length < 20 || BitConverter.ToUInt32(spirv, 0) != 0x07230203) throw new Exception("mesh: not a SPIR-V module");
        var words = new uint[spirv.Length / 4];
        Buffer.BlockCopy(spirv, 0, words, 0, words.Length * 4);
        uint bound = words[3];
        var ins = new List<Insn>();
        for (int p = 5; p < words.Length;)
        {
            int n = (int)(words[p] >> 16), op = (int)(words[p] & 0xffff);
            if (n == 0 || p + n > words.Length) throw new Exception("mesh: malformed SPIR-V");
            ins.Add(new Insn(op, new ArraySegment<uint>(words, p + 1, n - 1)));
            p += n;
        }
        uint Id() => bound++;

        // ---- storage buffers: Uniform + BufferBlock -> StorageBuffer + Block
        var bufferBlocks = new HashSet<uint>();
        foreach (var i in ins)
            if (i.Op == OpDecorate && i.W[1] == DecBufferBlock) { bufferBlocks.Add(i.W[0]); i.W[1] = DecBlock; }
        var pointee = new Dictionary<uint, (uint sc, uint type)>();   // pointer type -> storage class, pointee
        var ssboPointerTypes = new HashSet<uint>();
        foreach (var i in ins)
            if (i.Op == OpTypePointer)
            {
                if (i.W[1] == ScUniform && bufferBlocks.Contains(i.W[2])) { i.W[1] = ScStorageBuffer; ssboPointerTypes.Add(i.W[0]); }
                pointee[i.W[0]] = (i.W[1], i.W[2]);
            }
        var ssboPointers = new HashSet<uint>();
        var newTypes = new List<Insn>();   // inserted before the first function
        var sbPointerOf = new Dictionary<uint, uint>();   // pointee -> StorageBuffer pointer type
        uint StoragePointer(uint type)
        {
            if (sbPointerOf.TryGetValue(type, out uint t)) return t;
            foreach (var (pt, (sc, pe)) in pointee) if (sc == ScStorageBuffer && pe == type) { sbPointerOf[type] = pt; return pt; }
            t = Id();
            newTypes.Add(new Insn(OpTypePointer, new[] { t, (uint)ScStorageBuffer, type }));
            pointee[t] = (ScStorageBuffer, type);
            sbPointerOf[type] = t;
            return t;
        }
        foreach (var i in ins)
        {
            if (i.Op == OpVariable && ssboPointerTypes.Contains(i.W[0])) { i.W[2] = ScStorageBuffer; ssboPointers.Add(i.W[1]); }
            else if (i.Op is OpAccessChain or OpInBoundsAccessChain or OpPtrAccessChain or OpInBoundsPtrAccessChain && ssboPointers.Contains(i.W[2]))
            {
                var (sc, pe) = pointee[i.W[0]];
                if (sc != ScStorageBuffer) i.W[0] = StoragePointer(pe);
                ssboPointers.Add(i.W[1]);
            }
        }

        // ---- existing types and ids
        uint Find(Func<Insn, bool> f) { var i = ins.FirstOrDefault(f); return i == null ? 0 : i.W[0]; }
        int firstFunction = ins.FindIndex(i => i.Op == OpFunction);
        var globals = new List<Insn>();   // new types, constants, variables, inserted before the first function
        uint Ty(int op, params uint[] operands)
        {
            uint t = Find(i => i.Op == op && i.W.Skip(1).SequenceEqual(operands));
            if (t == 0) t = newTypes.Concat(globals).FirstOrDefault(i => i.Op == op && i.W.Skip(1).SequenceEqual(operands))?.W[0] ?? 0;
            if (t != 0) return t;
            t = Id();
            globals.Add(new Insn(op, new[] { t }.Concat(operands)));
            return t;
        }
        uint tVoid = Ty(OpTypeVoid), tBool = Ty(OpTypeBool), tUint = Ty(OpTypeInt, 32, 0), tFloat = Ty(OpTypeFloat, 32);
        uint tFnVoid = Ty(OpTypeFunction, tVoid);
        var constants = new Dictionary<uint, uint>();
        uint C(uint v)
        {
            if (constants.TryGetValue(v, out uint id)) return id;
            id = Id();
            globals.Add(new Insn(OpConstant, new[] { tUint, id, v }));
            constants[v] = id;
            return id;
        }
        uint Ptr(int sc, uint type) { uint t = Id(); globals.Add(new Insn(OpTypePointer, new[] { t, (uint)sc, type })); return t; }
        uint Arr(uint elem, uint n) { uint t = Id(); globals.Add(new Insn(OpTypeArray, new[] { t, elem, C(n) })); return t; }
        var decorations = new List<Insn>();
        var interfaceVars = new List<uint>();
        uint Var(uint ptrType, int sc, int builtin = -1, int location = -1)
        {
            uint v = Id();
            globals.Add(new Insn(OpVariable, new[] { ptrType, v, (uint)sc }));
            if (builtin >= 0) decorations.Add(new Insn(OpDecorate, new[] { v, (uint)DecBuiltIn, (uint)builtin }));
            if (location >= 0) decorations.Add(new Insn(OpDecorate, new[] { v, (uint)DecLocation, (uint)location }));
            return v;
        }

        var shared = ins.Where(i => i.Op == OpVariable && i.W[2] == ScWorkgroup).ToList();
        if (shared.Count != 1) throw new Exception($"mesh: {shared.Count} workgroup variables (one shared memory array expected)");
        uint sharedVar = shared[0].W[1];
        uint pSharedUint = Ptr(ScWorkgroup, tUint);
        var entry = ins.Single(i => i.Op == OpEntryPoint);
        if (entry.W[0] != ModelGLCompute) throw new Exception("mesh: the translator's entry point is not a compute one");
        uint mainFn = entry.W[1];
        uint glsl = Find(i => i.Op == OpExtInstImport && Str("GLSL.std.450").SequenceEqual(i.W.Skip(1)));
        var preamble = new List<Insn>();   // capability, extension, import: inserted after the last capability
        if (glsl == 0) { glsl = Id(); preamble.Add(new Insn(OpExtInstImport, new[] { glsl }.Concat(Str("GLSL.std.450")))); }

        uint pInputUint = Ptr(ScInput, tUint);
        uint lii = Var(pInputUint, ScInput, BiLocalInvocationIndex);
        int payloadWords = m.PayloadBytes / 4;
        uint payloadVar = 0, pPayloadUint = 0;
        if (m.Stage == 6 || m.HasTask)
        {
            if (payloadWords == 0) throw new Exception("mesh: a task payload of size 0");
            payloadVar = Var(Ptr(ScTaskPayload, Arr(tUint, (uint)payloadWords)), ScTaskPayload);
            pPayloadUint = Ptr(ScTaskPayload, tUint);
        }

        // ---- the entry function body
        var body = new List<Insn>();
        uint Emit(int op, uint type, params uint[] operands) { uint r = Id(); body.Add(new Insn(op, new[] { type, r }.Concat(operands))); return r; }
        void Do(int op, params uint[] operands) => body.Add(new Insn(op, operands));
        uint Label() { uint l = Id(); Do(OpLabel, l); return l; }
        uint Add(uint a, uint b) => Emit(OpIAdd, tUint, a, b);
        uint SharedWord(uint index) => Emit(OpLoad, tUint, Emit(OpAccessChain, pSharedUint, sharedVar, index));
        void Barrier() => Do(OpControlBarrier, C(2), C(2), C(0x108));   // Workgroup, Workgroup, AcquireRelease | WorkgroupMemory
        uint UMin(uint a, uint b) => Emit(OpExtInst, tUint, glsl, GlslUMin, a, b);
        // if (a < b) { then() }
        void If(uint a, uint b, Action then)
        {
            uint c = Emit(OpULessThan, tBool, a, b), t = Id(), merge = Id();
            Do(OpSelectionMerge, merge, 0);
            Do(OpBranchConditional, c, t, merge);
            Do(OpLabel, t);
            then();
            Do(OpBranch, merge);
            Do(OpLabel, merge);
        }
        // Words i, i + threads, ... below words of shared memory from word sharedAt <-> the payload variable.
        void CopyPayload(uint tid, bool toShared)
        {
            int threads = Math.Max(1, m.Threads);
            for (int k = 0; k * threads < payloadWords; k++)
            {
                uint idx = k == 0 ? tid : Add(tid, C((uint)(k * threads)));
                void copy()
                {
                    uint pp = Emit(OpAccessChain, pPayloadUint, payloadVar, idx);
                    uint sp = Emit(OpAccessChain, pSharedUint, sharedVar, Add(idx, C((uint)(m.Pay / 4))));
                    if (toShared) Do(OpStore, sp, Emit(OpLoad, tUint, pp));
                    else Do(OpStore, pp, Emit(OpLoad, tUint, sp));
                }
                if ((k + 1) * threads > payloadWords) If(idx, C((uint)payloadWords), copy);
                else copy();
            }
        }

        uint entryFn = Id();
        body.Add(new Insn(OpFunction, new[] { tVoid, entryFn, 0u, tFnVoid }));
        Label();
        uint tid = Emit(OpLoad, tUint, lii);
        // The counts start at 0: a task that culls everything never writes its mesh count, and the word then held what
        // the workgroup's shared memory last had, up to tens of thousands of mesh groups and a lost device; a mesh
        // shader that emits nothing leaves its primitive count the same way.
        uint countWord = C((uint)(m.Stage == 6 ? (m.Pay + 4) / 4 : m.Map / 4));
        If(tid, C(1), () => Do(OpStore, Emit(OpAccessChain, pSharedUint, sharedVar, countWord), C(0)));
        if (m.Stage == 7 && m.HasTask) CopyPayload(tid, true);
        Barrier();
        Emit(OpFunctionCall, tVoid, mainFn);
        Barrier();
        var modes = new List<Insn>();
        if (m.Stage == 6)
        {
            // one mesh group per task thread at most: a garbage count cannot launch more than the task could fill
            uint count = UMin(SharedWord(C((uint)(m.Pay + 4) / 4)), C((uint)Math.Max(1, m.Threads)));
            CopyPayload(tid, false);
            Do(OpEmitMeshTasksEXT, count, C(1), C(1), payloadVar);
        }
        else
        {
            int maxV = m.MaxVertices, maxP = m.MaxPrimitives, ipp = m.IndicesPerPrimitive, threads = Math.Max(1, m.Threads);
            if (maxV <= 0 || maxP <= 0) throw new Exception("mesh: no vertex or primitive limit");
            // outputs
            uint tV4 = Ty(OpTypeVector, tFloat, 4);
            uint pOutFloat = Ptr(ScOutput, tFloat);
            uint posVar = 0, sizeVar = 0;
            var generic = new Dictionary<int, uint>();
            foreach (int a in m.Lines)
            {
                if (a >= 0x70 && a < 0x80 && posVar == 0) posVar = Var(Ptr(ScOutput, Arr(tV4, (uint)maxV)), ScOutput, BiPosition);
                else if (a == 0x6c && sizeVar == 0) sizeVar = Var(Ptr(ScOutput, Arr(tFloat, (uint)maxV)), ScOutput, BiPointSize);
                else if (a >= 0x80 && !generic.ContainsKey((a - 0x80) / 16))
                    generic[(a - 0x80) / 16] = Var(Ptr(ScOutput, Arr(tV4, (uint)maxV)), ScOutput, -1, (a - 0x80) / 16);
            }
            uint tIdx = ipp == 1 ? tUint : Ty(OpTypeVector, tUint, (uint)ipp);
            int idxBuiltin = ipp == 1 ? BiPrimitivePointIndices : ipp == 2 ? BiPrimitiveLineIndices : BiPrimitiveTriangleIndices;
            uint idxVar = Var(Ptr(ScOutput, Arr(tIdx, (uint)maxP)), ScOutput, idxBuiltin);
            uint pOutIdx = Ptr(ScOutput, tIdx);

            uint count = UMin(SharedWord(C((uint)m.Map / 4)), C((uint)maxP));
            Do(OpSetMeshOutputsEXT, C((uint)maxV), count);
            int lines = m.Lines.Count;
            for (int k = 0; k * threads < maxV; k++)
            {
                uint v = k == 0 ? tid : Add(tid, C((uint)(k * threads)));
                If(v, C((uint)maxV), () =>
                {
                    // word ((v >> 5) * lines + line) * 32 + (v & 31) of the vertex area
                    uint group = Emit(OpShiftRightLogical, tUint, v, C(5)), lane = Emit(OpBitwiseAnd, tUint, v, C(31));
                    uint at = Add(Add(Emit(OpIMul, tUint, group, C((uint)(lines * 32))), lane), C((uint)m.Attr / 4));
                    for (int l = 0; l < lines; l++)
                    {
                        int a = m.Lines[l];
                        uint var = 0, comp = uint.MaxValue;
                        if (a >= 0x70 && a < 0x80) (var, comp) = (posVar, (uint)(a - 0x70) / 4);
                        else if (a == 0x6c) var = sizeVar;
                        else if (a >= 0x80) (var, comp) = (generic[(a - 0x80) / 16], (uint)((a - 0x80) % 16) / 4);
                        if (var == 0) continue;   // layer, viewport index: per primitive in Vulkan, not written
                        uint value = Emit(OpBitcast, tFloat, SharedWord(Add(at, C((uint)(l * 32)))));
                        uint ptr = comp == uint.MaxValue ? Emit(OpAccessChain, pOutFloat, var, v) : Emit(OpAccessChain, pOutFloat, var, v, C(comp));
                        Do(OpStore, ptr, value);
                    }
                });
            }
            for (int k = 0; k * threads < maxP; k++)
            {
                uint p = k == 0 ? tid : Add(tid, C((uint)(k * threads)));
                If(p, count, () =>
                {
                    // byte 4 + ipp * p + j of the primitive area
                    uint first = Add(Emit(OpIMul, tUint, p, C((uint)ipp)), C((uint)m.Map + 4));
                    var idx = new uint[ipp];
                    for (int j = 0; j < ipp; j++)
                    {
                        uint b = j == 0 ? first : Add(first, C((uint)j));
                        uint word = SharedWord(Emit(OpShiftRightLogical, tUint, b, C(2)));
                        uint shift = Emit(OpIMul, tUint, Emit(OpBitwiseAnd, tUint, b, C(3)), C(8));
                        uint value = Emit(OpBitwiseAnd, tUint, Emit(OpShiftRightLogical, tUint, word, shift), C(0xff));
                        idx[j] = UMin(value, C((uint)maxV - 1));
                    }
                    uint composite = ipp == 1 ? idx[0] : Emit(OpCompositeConstruct, tIdx, idx);
                    Do(OpStore, Emit(OpAccessChain, pOutIdx, idxVar, p), composite);
                });
            }
            Do(OpReturn);
            modes.Add(new Insn(OpExecutionMode, new[] { entryFn, (uint)ModeOutputVertices, (uint)maxV }));
            modes.Add(new Insn(OpExecutionMode, new[] { entryFn, (uint)ModeOutputPrimitives, (uint)maxP }));
            modes.Add(new Insn(OpExecutionMode, new[] { entryFn, (uint)(ipp == 1 ? ModeOutputPoints : ipp == 2 ? ModeOutputLines : ModeOutputTriangles) }));
        }
        body.Add(new Insn(OpFunctionEnd, Array.Empty<uint>()));

        // ---- reassemble
        foreach (var i in ins.Where(i => i.Op == OpExecutionMode && i.W[0] == mainFn)) i.W[0] = entryFn;
        preamble.Insert(0, new Insn(OpExtension, Str("SPV_EXT_mesh_shader")));
        preamble.Insert(0, new Insn(OpCapability, new[] { (uint)CapMeshShading }));
        int lastCap = ins.FindLastIndex(i => i.Op == OpCapability);
        // the entry point: the new model and function, every global variable as its interface (SPIR-V 1.4)
        var name = Str("main");
        var globalVars = ins.Take(firstFunction).Where(i => i.Op == OpVariable && i.W[2] != ScFunction).Select(i => i.W[1])
                            .Concat(globals.Where(i => i.Op == OpVariable).Select(i => i.W[1])).ToList();
        entry.W = new List<uint> { (uint)(m.Stage == 6 ? ModelTask : ModelMesh), entryFn }.Concat(name).Concat(globalVars).ToList();
        int lastMode = ins.FindLastIndex(i => i.Op == OpExecutionMode || i.Op == OpEntryPoint);
        int firstType = ins.FindIndex(i => i.Op >= OpTypeVoid && i.Op <= 39 || i.Op == OpConstant || i.Op == OpVariable);
        var result = new List<Insn>();
        for (int k = 0; k < ins.Count; k++)
        {
            if (k == firstType) result.AddRange(decorations);
            if (k == firstFunction) { result.AddRange(newTypes); result.AddRange(globals); }
            result.Add(ins[k]);
            if (k == lastCap) result.AddRange(preamble);
            if (k == lastMode) result.AddRange(modes);
        }
        result.AddRange(body);

        var output = new List<uint> { 0x07230203, 0x00010400, words[2], bound, 0 };
        foreach (var i in result) { output.Add((uint)((i.W.Count + 1) << 16 | i.Op)); output.AddRange(i.W); }
        var bytes = new byte[output.Count * 4];
        Buffer.BlockCopy(output.ToArray(), 0, bytes, 0, bytes.Length);
        return bytes;
    }

    // ---- the fallback for a GPU without VK_EXT_mesh_shader (job "mesh": {"fallback": true})
    //
    // The module stays a compute shader. A draw then is: the task program dispatched (task workgroups, 1, 1), the mesh
    // program dispatched (FallbackMeshPerTask, task workgroups, 1), or (mesh workgroups, 1, 1) without a task stage, and
    // an indexed draw of what they wrote, with a vertex shader that passes the vertices on (runtime/vk_exec_draw.c). Both
    // programs write into one storage buffer of 32-bit words at set 1, binding 0, the same one for every draw:
    //   header, words: 0 the draw's index count (words 0-4 are a VkDrawIndexedIndirectCommand; 1-4 the runtime's)
    //                  5 task records the task area holds, 6 slots the vertex and index areas hold
    //                  7, 8, 9 the first word of the task, vertex and index areas
    //                  10 indices per slot (primitive limit * indices per primitive), 11 slots handed out so far
    //                  12 words per task record
    //   task record t: the first slot of the task's mesh workgroups, their count, then its payload
    //   vertex area: FallbackStride words per vertex: the position, each generic vector GenericLocations names in that
    //                order, then the point size if the program writes one. Vertex 0 belongs to the runtime (a position
    //                outside the clip volume); slot s has vertices 1 + s * vertex limit onward.
    //   index area: per slot the primitives' vertex indices, 32 bits each; a primitive past the slot's count gets
    //               vertex 0 for every corner, which draws nothing.
    // A mesh workgroup's slot: its index without a task stage; after one, the task's first slot plus the workgroup's
    // index among the task's. A task takes its slots with an atomic add, so only the mesh workgroups tasks launch take
    // room, and one that finds the areas full launches fewer. Workgroups past the header's limits leave at once.
    // An output component the program never writes is 0, or 1 in w, as an unwritten vertex output reads on the console.
    public const int FallbackMeshPerTask = 32;   // mesh workgroups a task launches at most: its thread count (Convert)

    public static bool FallbackPointSize(AmpereLowering.MeshInfo m) => m.Lines.Contains(0x6c);
    public static int FallbackStride(AmpereLowering.MeshInfo m) => 4 * (1 + GenericLocations(m).Count() + (FallbackPointSize(m) ? 1 : 0));

    public static byte[] ConvertFallback(byte[] spirv, AmpereLowering.MeshInfo m)
    {
        if (spirv.Length < 20 || BitConverter.ToUInt32(spirv, 0) != 0x07230203) throw new Exception("mesh: not a SPIR-V module");
        var words = new uint[spirv.Length / 4];
        Buffer.BlockCopy(spirv, 0, words, 0, words.Length * 4);
        if (words[1] >= 0x00010400) throw new Exception("mesh fallback: a SPIR-V 1.3 module expected (BufferBlock)");
        uint bound = words[3];
        var ins = new List<Insn>();
        for (int p = 5; p < words.Length;)
        {
            int n = (int)(words[p] >> 16), op = (int)(words[p] & 0xffff);
            if (n == 0 || p + n > words.Length) throw new Exception("mesh: malformed SPIR-V");
            ins.Add(new Insn(op, new ArraySegment<uint>(words, p + 1, n - 1)));
            p += n;
        }
        uint Id() => bound++;
        const int OpTypeRuntimeArray = 29, OpTypeStruct = 30, OpMemberDecorate = 72, OpISub = 130, OpUGreaterThanEqual = 174,
            OpSelect = 169, OpAtomicIAdd = 234, OpAtomicUMax = 239;
        const int DecArrayStride = 6, DecBinding = 33, DecDescriptorSet = 34, DecOffset = 35;
        const int BiNumWorkgroups = 24, BiWorkgroupId = 26;
        const uint HIndexCount = 0, HTaskCap = 5, HSlotCap = 6, HTaskAt = 7, HVertexAt = 8, HIndexAt = 9, HSlotIndices = 10,
            HSlots = 11, HTaskWords = 12;

        uint Find(Func<Insn, bool> f) { var i = ins.FirstOrDefault(f); return i == null ? 0 : i.W[0]; }
        int firstFunction = ins.FindIndex(i => i.Op == OpFunction);
        var globals = new List<Insn>();       // new types, constants, variables, inserted before the first function
        var decorations = new List<Insn>();   // inserted before the first type
        uint Ty(int op, params uint[] operands)
        {
            uint t = Find(i => i.Op == op && i.W.Skip(1).SequenceEqual(operands));
            if (t == 0) t = globals.FirstOrDefault(i => i.Op == op && i.W.Skip(1).SequenceEqual(operands))?.W[0] ?? 0;
            if (t != 0) return t;
            t = Id();
            globals.Add(new Insn(op, new[] { t }.Concat(operands)));
            return t;
        }
        uint tVoid = Ty(OpTypeVoid), tBool = Ty(OpTypeBool), tUint = Ty(OpTypeInt, 32, 0);
        uint tFnVoid = Ty(OpTypeFunction, tVoid), tUint3 = Ty(OpTypeVector, tUint, 3);
        var constants = new Dictionary<uint, uint>();
        uint C(uint v)
        {
            if (constants.TryGetValue(v, out uint id)) return id;
            id = Id();
            globals.Add(new Insn(OpConstant, new[] { tUint, id, v }));
            constants[v] = id;
            return id;
        }
        uint Ptr(int sc, uint type) { uint t = Id(); globals.Add(new Insn(OpTypePointer, new[] { t, (uint)sc, type })); return t; }

        var shared = ins.Where(i => i.Op == OpVariable && i.W[2] == ScWorkgroup).ToList();
        if (shared.Count != 1) throw new Exception($"mesh: {shared.Count} workgroup variables (one shared memory array expected)");
        uint sharedVar = shared[0].W[1];
        uint pSharedUint = Ptr(ScWorkgroup, tUint);
        var entry = ins.Single(i => i.Op == OpEntryPoint);
        if (entry.W[0] != ModelGLCompute) throw new Exception("mesh: the translator's entry point is not a compute one");
        uint mainFn = entry.W[1];
        uint glsl = Find(i => i.Op == OpExtInstImport && Str("GLSL.std.450").SequenceEqual(i.W.Skip(1)));
        var preamble = new List<Insn>();
        if (glsl == 0) { glsl = Id(); preamble.Add(new Insn(OpExtInstImport, new[] { glsl }.Concat(Str("GLSL.std.450")))); }

        // built-in inputs: the module's own variable where it has one (an entry point takes one per built-in)
        uint pInputUint = Ptr(ScInput, tUint);
        var newInputs = new List<uint>();
        uint Input(int builtin, uint type)
        {
            var d = ins.FirstOrDefault(i => i.Op == OpDecorate && i.W.Count == 3 && i.W[1] == DecBuiltIn && i.W[2] == (uint)builtin);
            if (d != null) return d.W[0];
            uint v = Id();
            globals.Add(new Insn(OpVariable, new[] { Ptr(ScInput, type), v, (uint)ScInput }));
            decorations.Add(new Insn(OpDecorate, new[] { v, (uint)DecBuiltIn, (uint)builtin }));
            newInputs.Add(v);
            return v;
        }
        uint lii = Input(BiLocalInvocationIndex, tUint), workgroupId = Input(BiWorkgroupId, tUint3);

        // the buffer: struct { uint word[]; } at set 1, binding 0
        uint tWords = Id(), tBuffer = Id(), buffer = Id();
        globals.Add(new Insn(OpTypeRuntimeArray, new[] { tWords, tUint }));
        globals.Add(new Insn(OpTypeStruct, new[] { tBuffer, tWords }));
        globals.Add(new Insn(OpVariable, new[] { Ptr(ScUniform, tBuffer), buffer, (uint)ScUniform }));
        uint pBufferUint = Ptr(ScUniform, tUint);
        decorations.Add(new Insn(OpDecorate, new[] { tWords, (uint)DecArrayStride, 4u }));
        decorations.Add(new Insn(OpDecorate, new[] { tBuffer, (uint)DecBufferBlock }));
        decorations.Add(new Insn(OpMemberDecorate, new[] { tBuffer, 0u, (uint)DecOffset, 0u }));
        decorations.Add(new Insn(OpDecorate, new[] { buffer, (uint)DecDescriptorSet, 1u }));
        decorations.Add(new Insn(OpDecorate, new[] { buffer, (uint)DecBinding, 0u }));

        var body = new List<Insn>();
        uint Emit(int op, uint type, params uint[] operands) { uint r = Id(); body.Add(new Insn(op, new[] { type, r }.Concat(operands))); return r; }
        void Do(int op, params uint[] operands) => body.Add(new Insn(op, operands));
        uint Add(uint a, uint b) => Emit(OpIAdd, tUint, a, b);
        uint Mul(uint a, uint b) => Emit(OpIMul, tUint, a, b);
        uint UMin(uint a, uint b) => Emit(OpExtInst, tUint, glsl, GlslUMin, a, b);
        uint SharedPtr(uint index) => Emit(OpAccessChain, pSharedUint, sharedVar, index);
        uint SharedWord(uint index) => Emit(OpLoad, tUint, SharedPtr(index));
        uint WordPtr(uint index) => Emit(OpAccessChain, pBufferUint, buffer, C(0), index);
        uint Word(uint index) => Emit(OpLoad, tUint, WordPtr(index));
        void SetWord(uint index, uint value) => Do(OpStore, WordPtr(index), value);
        uint Header(uint word) => Word(C(word));
        uint InputX(uint variable, uint component) => Emit(OpLoad, tUint, Emit(OpAccessChain, pInputUint, variable, C(component)));
        void Barrier() => Do(OpControlBarrier, C(2), C(2), C(0x108));   // Workgroup, Workgroup, AcquireRelease | WorkgroupMemory
        // if (a < b) { then() }
        void If(uint a, uint b, Action then)
        {
            uint c = Emit(OpULessThan, tBool, a, b), t = Id(), merge = Id();
            Do(OpSelectionMerge, merge, 0);
            Do(OpBranchConditional, c, t, merge);
            Do(OpLabel, t);
            then();
            Do(OpBranch, merge);
            Do(OpLabel, merge);
        }
        // if (a >= b) return;   the same for every thread of the workgroup wherever it is used
        void LeaveIf(uint a, uint b)
        {
            uint c = Emit(OpUGreaterThanEqual, tBool, a, b), t = Id(), merge = Id();
            Do(OpSelectionMerge, merge, 0);
            Do(OpBranchConditional, c, t, merge);
            Do(OpLabel, t);
            Do(OpReturn);
            Do(OpLabel, merge);
        }
        int threads = Math.Max(1, m.Threads), payloadWords = m.PayloadBytes / 4;
        // Payload words i, i + threads, ... between shared memory and the task record at word `record`.
        void CopyPayload(uint tid, uint record, bool toShared)
        {
            for (int k = 0; k * threads < payloadWords; k++)
            {
                uint idx = k == 0 ? tid : Add(tid, C((uint)(k * threads)));
                void copy()
                {
                    uint sp = SharedPtr(Add(idx, C((uint)(m.Pay / 4))));
                    uint at = Add(Add(record, C(2)), idx);
                    if (toShared) Do(OpStore, sp, Word(at));
                    else SetWord(at, Emit(OpLoad, tUint, sp));
                }
                if ((k + 1) * threads > payloadWords) If(idx, C((uint)payloadWords), copy);
                else copy();
            }
        }

        uint entryFn = Id();
        body.Add(new Insn(OpFunction, new[] { tVoid, entryFn, 0u, tFnVoid }));
        Do(OpLabel, Id());
        uint tid = Emit(OpLoad, tUint, lii), x = InputX(workgroupId, 0);
        if (m.Stage == 6)
        {
            if (payloadWords == 0) throw new Exception("mesh: a task payload of size 0");
            uint countWord = C((uint)(m.Pay + 4) / 4);
            LeaveIf(x, Header(HTaskCap));   // no room for this task's record
            If(tid, C(1), () => Do(OpStore, SharedPtr(countWord), C(0)));
            Barrier();
            Emit(OpFunctionCall, tVoid, mainFn);
            Barrier();
            uint record = Add(Header(HTaskAt), Mul(x, Header(HTaskWords)));
            If(tid, C(1), () =>
            {
                // one mesh group per task thread at most, and no more than the slots left
                uint count = UMin(SharedWord(countWord), C((uint)Math.Min(threads, FallbackMeshPerTask)));
                uint cap = Header(HSlotCap);
                uint first = UMin(Emit(OpAtomicIAdd, tUint, WordPtr(C(HSlots)), C(1), C(0), count), cap);
                uint taken = UMin(count, Emit(OpISub, tUint, cap, first));
                Emit(OpAtomicUMax, tUint, WordPtr(C(HIndexCount)), C(1), C(0), Mul(Add(first, taken), Header(HSlotIndices)));
                SetWord(record, first);
                SetWord(Add(record, C(1)), taken);
            });
            CopyPayload(tid, record, false);
        }
        else
        {
            int maxV = m.MaxVertices, maxP = m.MaxPrimitives, ipp = m.IndicesPerPrimitive;
            if (maxV <= 0 || maxP <= 0) throw new Exception("mesh: no vertex or primitive limit");
            uint slot;
            if (m.HasTask)
            {
                if (payloadWords == 0) throw new Exception("mesh: a task payload of size 0");
                uint task = InputX(workgroupId, 1);
                LeaveIf(task, Header(HTaskCap));
                uint record = Add(Header(HTaskAt), Mul(task, Header(HTaskWords)));
                LeaveIf(x, Word(Add(record, C(1))));   // past the mesh workgroups the task launched
                slot = Add(Word(record), x);
                CopyPayload(tid, record, true);
            }
            else
            {
                uint cap = Header(HSlotCap);
                LeaveIf(x, cap);
                slot = x;
                uint groups = InputX(Input(BiNumWorkgroups, tUint3), 0);
                If(Add(tid, x), C(1), () => SetWord(C(HIndexCount), Mul(UMin(groups, cap), C((uint)(maxP * ipp)))));
            }
            uint countWord = C((uint)m.Map / 4);
            If(tid, C(1), () => Do(OpStore, SharedPtr(countWord), C(0)));
            Barrier();
            Emit(OpFunctionCall, tVoid, mainFn);
            Barrier();
            uint count = UMin(SharedWord(countWord), C((uint)maxP));
            uint firstVertex = Add(Mul(slot, C((uint)maxV)), C(1));

            // the vertex layout: which line holds each word of a vertex, -1 none
            var generics = GenericLocations(m).ToList();
            int stride = FallbackStride(m);
            var lineOf = Enumerable.Repeat(-1, stride).ToArray();
            for (int l = 0; l < m.Lines.Count; l++)
            {
                int a = m.Lines[l];
                if (a >= 0x70 && a < 0x80) lineOf[(a - 0x70) / 4] = l;
                else if (a == 0x6c) lineOf[4 * (1 + generics.Count)] = l;
                else if (a >= 0x80) lineOf[4 * (1 + generics.IndexOf((a - 0x80) / 16)) + (a - 0x80) % 16 / 4] = l;
            }
            int lines = m.Lines.Count;
            uint vertexAt = Add(Header(HVertexAt), Mul(firstVertex, C((uint)stride)));
            for (int k = 0; k * threads < maxV; k++)
            {
                uint v = k == 0 ? tid : Add(tid, C((uint)(k * threads)));
                If(v, C((uint)maxV), () =>
                {
                    // word ((v >> 5) * lines + line) * 32 + (v & 31) of the vertex area of shared memory
                    uint group = Emit(OpShiftRightLogical, tUint, v, C(5)), lane = Emit(OpBitwiseAnd, tUint, v, C(31));
                    uint at = Add(Add(Mul(group, C((uint)(lines * 32))), lane), C((uint)m.Attr / 4));
                    uint to = Add(vertexAt, Mul(v, C((uint)stride)));
                    for (int w = 0; w < stride; w++)
                    {
                        bool pointSize = FallbackPointSize(m) && w >= 4 * (1 + generics.Count);
                        if (pointSize && lineOf[w] < 0) continue;   // the three words after the point size
                        uint value = lineOf[w] >= 0 ? SharedWord(Add(at, C((uint)(lineOf[w] * 32)))) : C(w % 4 == 3 ? 0x3f800000u : 0u);
                        SetWord(w == 0 ? to : Add(to, C((uint)w)), value);
                    }
                });
            }
            uint indexAt = Add(Header(HIndexAt), Mul(slot, C((uint)(maxP * ipp))));
            for (int k = 0; k * threads < maxP; k++)
            {
                uint p = k == 0 ? tid : Add(tid, C((uint)(k * threads)));
                If(p, C((uint)maxP), () =>
                {
                    uint drawn = Emit(OpULessThan, tBool, p, count);
                    // byte 4 + ipp * p + j of the primitive area
                    uint first = Add(Mul(p, C((uint)ipp)), C((uint)m.Map + 4));
                    uint to = Add(indexAt, Mul(p, C((uint)ipp)));
                    for (int j = 0; j < ipp; j++)
                    {
                        uint b = j == 0 ? first : Add(first, C((uint)j));
                        uint word = SharedWord(Emit(OpShiftRightLogical, tUint, b, C(2)));
                        uint shift = Mul(Emit(OpBitwiseAnd, tUint, b, C(3)), C(8));
                        uint value = Emit(OpBitwiseAnd, tUint, Emit(OpShiftRightLogical, tUint, word, shift), C(0xff));
                        uint vertex = Add(UMin(value, C((uint)maxV - 1)), firstVertex);
                        SetWord(j == 0 ? to : Add(to, C((uint)j)), Emit(OpSelect, tUint, drawn, vertex, C(0)));
                    }
                });
            }
        }
        Do(OpReturn);
        body.Add(new Insn(OpFunctionEnd, Array.Empty<uint>()));

        // ---- reassemble
        foreach (var i in ins.Where(i => i.Op == OpExecutionMode && i.W[0] == mainFn)) i.W[0] = entryFn;
        entry.W[1] = entryFn;
        entry.W.AddRange(newInputs);
        int lastCap = ins.FindLastIndex(i => i.Op == OpCapability);
        int firstType = ins.FindIndex(i => i.Op >= OpTypeVoid && i.Op <= 39 || i.Op == OpConstant || i.Op == OpVariable);
        var result = new List<Insn>();
        for (int k = 0; k < ins.Count; k++)
        {
            if (k == firstType) result.AddRange(decorations);
            if (k == firstFunction) result.AddRange(globals);
            result.Add(ins[k]);
            if (k == lastCap) result.AddRange(preamble);
        }
        result.AddRange(body);

        var output = new List<uint> { 0x07230203, words[1], words[2], bound, 0 };
        foreach (var i in result) { output.Add((uint)((i.W.Count + 1) << 16 | i.Op)); output.AddRange(i.W); }
        var bytes = new byte[output.Count * 4];
        Buffer.BlockCopy(output.ToArray(), 0, bytes, 0, bytes.Length);
        return bytes;
    }
}
