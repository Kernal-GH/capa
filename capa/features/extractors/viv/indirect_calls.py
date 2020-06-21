import collections

import envi
import envi.archs.i386.disasm
import envi.archs.amd64.disasm
import vivisect.const


# pull out consts for lookup performance
i386RegOper = envi.archs.i386.disasm.i386RegOper
i386ImmOper = envi.archs.i386.disasm.i386ImmOper
i386ImmMemOper = envi.archs.i386.disasm.i386ImmMemOper
Amd64RipRelOper = envi.archs.amd64.disasm.Amd64RipRelOper
LOC_OP = vivisect.const.LOC_OP
IF_NOFALL = envi.IF_NOFALL
REF_CODE = vivisect.const.REF_CODE
FAR_BRANCH_MASK = (envi.BR_PROC | envi.BR_DEREF | envi.BR_ARCH)

DESTRUCTIVE_MNEMONICS = ('mov', 'lea', 'pop', 'xor')


def get_previous_instructions(vw, va):
    '''
    collect the instructions that flow to the given address, local to the current function.

    args:
      vw (vivisect.Workspace)
      va (int): the virtual address to inspect

    returns:
      List[int]: the prior instructions, which may fallthrough and/or jump here
    '''
    ret = []

    # find the immediate prior instruction.
    # ensure that it fallsthrough to this one.
    loc = vw.getPrevLocation(va, adjacent=True)
    if loc is not None:
        # from vivisect.const:
        # location: (L_VA, L_SIZE, L_LTYPE, L_TINFO)
        (pva, _, ptype, pinfo) = vw.getPrevLocation(va, adjacent=True)

        if ptype == LOC_OP and not (pinfo & IF_NOFALL):
            ret.append(pva)

    # find any code refs, e.g. jmp, to this location.
    # ignore any calls.
    #
    # from vivisect.const:
    # xref: (XR_FROM, XR_TO, XR_RTYPE, XR_RFLAG)
    for (xfrom, _, _, xflag) in vw.getXrefsTo(va, REF_CODE):
        if (xflag & FAR_BRANCH_MASK) != 0:
            continue
        ret.append(xfrom)

    return ret


class NotFoundError(Exception):
    pass


def find_definition(vw, va, reg):
    '''
    scan backwards from the given address looking for assignments to the given register.
    if a constant, return that value.

    args:
      vw (vivisect.Workspace)
      va (int): the virtual address at which to start analysis
      reg (int): the vivisect register to study

    returns:
      (va: int, value?: int|None): the address of the assignment and the value, if a constant.

    raises:
      NotFoundError: when the definition cannot be found.
    '''
    q = collections.deque()
    seen = set([])

    q.extend(get_previous_instructions(vw, va))
    while q:
        cur = q.popleft()

        # skip if we've already processed this location
        if cur in seen:
            continue
        seen.add(cur)

        insn = vw.parseOpcode(cur)

        if len(insn.opers) == 0:
            q.extend(get_previous_instructions(vw, cur))
            continue

        opnd0 = insn.opers[0]
        if not \
                (isinstance(opnd0, i386RegOper)
                 and opnd0.reg == reg
                 and insn.mnem in DESTRUCTIVE_MNEMONICS):
            q.extend(get_previous_instructions(vw, cur))
            continue

        # if we reach here, the instruction is destructive to our target register.

        # we currently only support extracting the constant from something like: `mov $reg, IAT`
        # so, any other pattern results in an unknown value, represented by None.
        # this is a good place to extend in the future, if we need more robust support.
        if insn.mnem != 'mov':
            return (cur, None)
        else:
            opnd1 = insn.opers[1]
            if isinstance(opnd1, i386ImmOper):
                return (cur, opnd1.getOperValue(opnd1))
            elif isinstance(opnd1, i386ImmMemOper):
                return (cur, opnd1.getOperAddr(opnd1))
            elif isinstance(opnd1, Amd64RipRelOper):
                return (cur, opnd1.getOperAddr(insn))
            else:
                # might be something like: `mov $reg, dword_401000[eax]`
                return (cur, None)

    raise NotFoundError()


def is_indirect_call(vw, va, insn=None):
    if insn is None:
        insn = vw.parseOpcode(va)

    return (insn.mnem == 'call'
            and isinstance(insn.opers[0], envi.archs.i386.disasm.i386RegOper))


def resolve_indirect_call(vw, va, insn=None):
    '''
    inspect the given indirect call instruction and attempt to resolve the target address.

    args:
      vw (vivisect.Workspace)
      va (int): the virtual address at which to start analysis

    returns:
      (va: int, value?: int|None): the address of the assignment and the value, if a constant.

    raises:
      NotFoundError: when the definition cannot be found.
    '''
    if insn is None:
        insn = vw.parseOpcode(va)

    assert is_indirect_call(vw, va, insn=insn)

    return find_definition(vw, va, insn.opers[0].reg)