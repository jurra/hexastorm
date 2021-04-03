from math import ceil

from nmigen import Signal, Elaboratable, signed, Cat
from nmigen import Module
from nmigen.hdl.mem import Array

from luna.gateware.utils.cdc import synchronize
from luna.gateware.interface.spi import SPICommandInterface, SPIBus
from luna.gateware.memory import TransactionalizedFIFO

from FPGAG.resources import get_all_resources
from FPGAG.constants import (COMMAND_BYTES, WORD_BYTES, STATE, INSTRUCTIONS,
                             MEMWIDTH, COMMANDS, DEGREE, BIT_SHIFT,
                             MOVE_TICKS)


class SPIParser(Elaboratable):
    """ Parses and replies to commands over SPI

    The following commmands are possible
      status -- send back state of the peripheriral
      start  -- enable execution of gcode
      stop   -- halt execution of gcode
      write  -- write instruction to FIFO or report memory is full

    I/O signals:
        I/O: Spibus       -- spi bus connected to peripheral
        I: positions      -- positions of stepper motors
        I: pin state      -- state of certain pins
        I: read_commit    -- finalize read transactionalizedfifo
        I: read_en        -- enable read transactionalizedfifo
        I: dispatcherror  -- error while processing stored command from spi
        O: execute        -- start processing gcode
        O: read_data      -- read data from transactionalizedfifo
        O: empty          -- transactionalizedfifo is empty
    """
    def __init__(self, platform, top=False):
        """
        platform  -- used to pass test platform
        """
        self.platform = platform
        self.top = top

        self.spi = SPIBus()
        self.position = Array(Signal(signed(64))
                              for _ in range(platform.motors))
        self.pinstate = Signal(8)
        self.read_commit = Signal()
        self.read_en = Signal()
        self.dispatcherror = Signal()
        self.execute = Signal()
        self.read_data = Signal(MEMWIDTH)
        self.empty = Signal()

    def elaborate(self, platform):
        m = Module()
        if platform and self.top:
            board_spi = platform.request("debug_spi")
            spi2 = synchronize(m, board_spi)
            m.d.comb += self.spi.connect(spi2)
        if self.platform:
            platform = self.platform
        spi = self.spi
        interface = SPICommandInterface(command_size=COMMAND_BYTES*8,
                                        word_size=WORD_BYTES*8)
        m.d.comb += interface.spi.connect(spi)
        m.submodules.interface = interface
        # FIFO connection
        fifo = TransactionalizedFIFO(width=MEMWIDTH,
                                     depth=platform.memdepth)
        if platform.name == 'Test':
            self.fifo = fifo
        m.submodules.fifo = fifo
        m.d.comb += [self.read_data.eq(fifo.read_data),
                     fifo.read_commit.eq(self.read_commit),
                     fifo.read_en.eq(self.read_en),
                     self.empty.eq(fifo.empty)]
        # Peripheral state
        state = Signal(8)
        m.d.sync += [state[STATE.PARSING].eq(self.execute),
                     state[STATE.FULL].eq(
                     fifo.space_available <
                     ceil(platform.bytesinmove/WORD_BYTES)),
                     state[STATE.DISPATCHERROR].eq(self.dispatcherror)]
        # Parser
        mtrcntr = Signal(range(platform.motors))
        bytesreceived = Signal(range(platform.bytesinmove+1))
        with m.FSM(reset='RESET', name='parser'):
            with m.State('RESET'):
                m.d.sync += [self.execute.eq(0), bytesreceived.eq(0)]
                m.next = 'WAIT_COMMAND'
            with m.State('WAIT_COMMAND'):
                m.d.sync += [fifo.write_commit.eq(0)]
                with m.If(interface.command_ready):
                    word = Cat(state[::-1], self.pinstate[::-1])
                    with m.If(interface.command == COMMANDS.EMPTY):
                        m.next = 'WAIT_COMMAND'
                    with m.Elif(interface.command == COMMANDS.START):
                        m.next = 'WAIT_COMMAND'
                        m.d.sync += self.execute.eq(1)
                    with m.Elif(interface.command == COMMANDS.STOP):
                        m.next = 'WAIT_COMMAND'
                        m.d.sync += self.execute.eq(0)
                    with m.Elif(interface.command == COMMANDS.WRITE):
                        m.d.sync += interface.word_to_send.eq(word)
                        with m.If((state[STATE.FULL] == 0) |
                                  (bytesreceived != 0)):
                            m.next = 'WAIT_WORD'
                        with m.Else():
                            m.next = 'WAIT_COMMAND'
                    with m.Elif(interface.command == COMMANDS.READ):
                        m.d.sync += interface.word_to_send.eq(word)
                        m.next = 'WAIT_COMMAND'
                    with m.Elif(interface.command == COMMANDS.POSITION):
                        # position is requested multiple times for multiple
                        # motors
                        with m.If(mtrcntr < platform.motors):
                            m.d.sync += mtrcntr.eq(mtrcntr+1)
                        with m.Else():
                            m.d.sync += mtrcntr.eq(0)
                        m.d.sync += interface.word_to_send.eq(
                                                self.position[mtrcntr])
                        m.next = 'WAIT_COMMAND'
            with m.State('WAIT_WORD'):
                with m.If(interface.word_complete):
                    m.d.sync += [fifo.write_en.eq(1),
                                 bytesreceived.eq(bytesreceived+WORD_BYTES),
                                 fifo.write_data.eq(interface.word_received)]
                    m.next = 'WRITE'
            with m.State('WRITE'):
                m.d.sync += fifo.write_en.eq(0)
                m.next = 'WAIT_COMMAND'
                with m.If(bytesreceived >= platform.bytesinmove):
                    m.d.sync += [bytesreceived.eq(0),
                                 fifo.write_commit.eq(1)]
        return m


class Polynomal(Elaboratable):
    """ Sets motor states using a polynomal algorithm

        A polynomal up to 3 order, e.g. c*x^3+b*x^2+a*x,
        is evaluated using the assumption that x starts at 0
        and y starts at 0. The polynomal determines the stepper
        position. The bitshift bit determines
        the position. In every tick the step can at most increase
        with one count.

        I/O signals:
        I: coeff          -- polynomal coefficients
        I: start          -- start signal
        O: busy           -- busy signal
        O: finished       -- finished signal
        O: total steps    -- total steps executed in move
        O: dir            -- direction; 1 is postive and 0 is negative
        O: step           -- step signal
    """
    def __init__(self, platform=None, divider=50, top=False):
        ''' divider -- if sys clk is 50 MHz and divider is 50
                       motor state is update with 1 Mhz
        '''
        self.top = top
        self.divider = divider
        self.platform = platform
        self.order = DEGREE
        # change code for other orders
        assert self.order == 3
        self.motors = platform.motors
        self.max_steps = int(MOVE_TICKS/2)  # Nyquist
        # inputs
        self.coeff = Array()
        for _ in range(self.motors):
            self.coeff.extend([Signal(signed(64)),
                               Signal(signed(64)),
                               Signal(signed(64))])
        self.start = Signal()
        self.ticklimit = Signal(MOVE_TICKS.bit_length())
        # output
        self.busy = Signal()
        self.finished = Signal()
        self.totalsteps = Array(Signal(signed(self.max_steps.bit_length()+1))
                                for _ in range(self.motors))
        self.dir = Array(Signal() for _ in range(self.motors))
        self.step = Array(Signal() for _ in range(self.motors))

    def elaborate(self, platform):
        m = Module()
        # add 1 MHZ clock domain
        cntr = Signal(range(self.divider))
        # pos
        max_bits = (self.max_steps << BIT_SHIFT).bit_length()
        cntrs = Array(Signal(signed(max_bits+1))
                      for _ in range(len(self.coeff)))
        assert max_bits <= 64
        ticks = Signal(MOVE_TICKS.bit_length())
        if self.top:
            steppers = [res for res in get_all_resources(platform, "stepper")]
            assert len(steppers) != 0
            for idx, stepper in enumerate(steppers):
                m.d.comb += [stepper.step.eq(self.step[idx]),
                             stepper.dir.eq(self.dir[idx])]
        else:
            self.ticks = ticks
            self.cntrs = cntrs

        # steps
        for motor in range(self.motors):
            m.d.comb += [self.step[motor].eq(
                         cntrs[motor*self.order][BIT_SHIFT]),
                         self.totalsteps[motor].eq(
                         cntrs[motor*self.order] >> (BIT_SHIFT+1))]
        # directions
        counter_d = Array(Signal(signed(max_bits+1))
                          for _ in range(self.motors))
        for motor in range(self.motors):
            m.d.sync += counter_d[motor].eq(cntrs[motor*self.order])
            # negative case --> decreasing
            with m.If(counter_d[motor] > cntrs[motor*self.order]):
                m.d.sync += self.dir[motor].eq(0)
            # positive case --> increasing
            with m.Elif(counter_d[motor] < cntrs[motor*self.order]):
                m.d.sync += self.dir[motor].eq(1)
        with m.FSM(reset='RESET', name='polynomen'):
            with m.State('RESET'):
                m.next = 'WAIT_START'
                m.d.sync += [self.busy.eq(0),
                             self.finished.eq(0)]
            with m.State('WAIT_START'):
                m.d.sync += self.finished.eq(0)
                with m.If(self.start):
                    for motor in range(self.motors):
                        coef0 = motor*self.order
                        m.d.sync += [cntrs[coef0].eq(0),
                                     counter_d[motor].eq(0)]
                    m.d.sync += [self.busy.eq(1),
                                 self.finished.eq(0)]
                    m.next = 'RUNNING'
            with m.State('RUNNING'):
                with m.If((ticks < self.ticklimit) & (cntr >= self.divider-1)):
                    m.d.sync += [ticks.eq(ticks+1),
                                 cntr.eq(0)]
                    for motor in range(self.motors):
                        idx = motor*self.order
                        op3 = 3*2*self.coeff[idx+2] + cntrs[idx+2]
                        op2 = (cntrs[idx+2] + 2*self.coeff[idx+1]
                               + cntrs[idx+1])
                        op1 = (self.coeff[idx+2] + self.coeff[idx+1]
                               + self.coeff[idx] + cntrs[idx+2] +
                               cntrs[idx+1] + cntrs[idx])
                        m.d.sync += [cntrs[idx+2].eq(op3),
                                     cntrs[idx+1].eq(op2),
                                     cntrs[idx].eq(op1)]
                with m.Elif(ticks < self.ticklimit):
                    m.d.sync += cntr.eq(cntr+1)
                with m.Else():
                    m.d.sync += [ticks.eq(0),
                                 self.busy.eq(0),
                                 self.finished.eq(1)]
                    m.next = 'WAIT_START'
        return m


class Dispatcher(Elaboratable):
    """ Dispatches instructions to right submodule

        Instructions are buffered in SRAM. This module checks the buffer
        and dispatches the instructions to the corresponding module.
        This is the top module"""
    def __init__(self, platform=None, divider=50):
        """
        platform  -- used to pass test platform
        divider   -- if sys clk is 50 MHz and divider is 50
                     motor state is update with 1 Mhz
        """
        self.platform = platform
        self.divider = divider

    def elaborate(self, platform):
        m = Module()
        # Connect Parser
        parser = SPIParser(self.platform)
        m.submodules.parser = parser
        # Connect Polynomal Move module
        polynomal = Polynomal(self.platform, self.divider)
        m.submodules.polynomal = polynomal
        # Busy signal
        busy = Signal()
        m.d.comb += busy.eq(polynomal.busy)
        # position adder
        pol_finished_d = Signal()
        m.d.sync += pol_finished_d.eq(polynomal.finished)

        if platform:
            board_spi = platform.request("debug_spi")
            spi = synchronize(m, board_spi)
            m.submodules.car = platform.clock_domain_generator()
            steppers = [res for res in get_all_resources(platform, "stepper")]
            assert len(steppers) != 0
        else:
            platform = self.platform
            self.spi = SPIBus()
            self.parser = parser
            self.pol = polynomal
            spi = synchronize(m, self.spi)
            self.steppers = steppers = platform.steppers
            aux = platform.aux
            self.aux = aux
            self.busy = busy
        coeffcnt = Signal(range(len(polynomal.coeff)))
        # connect motors
        for idx, stepper in enumerate(steppers):
            m.d.comb += [stepper.step.eq(polynomal.step[idx] &
                                         (stepper.limit == 0)),
                         stepper.dir.eq(polynomal.dir[idx]),
                         parser.pinstate[idx].eq(stepper.limit)]
        # connect spi
        m.d.comb += parser.spi.connect(spi)
        with m.If((pol_finished_d == 0) & polynomal.finished):
            for idx, position in enumerate(parser.position):
                m.d.sync += position.eq(position+polynomal.totalsteps[idx])
        with m.FSM(reset='RESET', name='dispatcher'):
            with m.State('RESET'):
                m.next = 'WAIT_INSTRUCTION'
            with m.State('WAIT_INSTRUCTION'):
                m.d.sync += [parser.read_commit.eq(0), polynomal.start.eq(0)]
                with m.If((parser.empty == 0) & parser.execute & (busy == 0)):
                    m.d.sync += parser.read_en.eq(1)
                    m.next = 'PARSEHEAD'
            # check which instruction we r handling
            with m.State('PARSEHEAD'):
                with m.If(parser.read_data[:8] == INSTRUCTIONS.MOVE):
                    m.d.sync += [polynomal.ticklimit.eq(parser.read_data[8:]),
                                 parser.read_en.eq(0),
                                 coeffcnt.eq(0)]
                    m.next = 'MOVE_POLYNOMAL'
                with m.Else():
                    m.next = 'ERROR'
                    m.d.sync += parser.dispatcherror.eq(1)
            with m.State('MOVE_POLYNOMAL'):
                with m.If(parser.read_en == 0):
                    m.d.sync += parser.read_en.eq(1)
                with m.Elif(coeffcnt < len(polynomal.coeff)):
                    m.d.sync += [polynomal.coeff[coeffcnt].eq(
                                 parser.read_data),
                                 coeffcnt.eq(coeffcnt+1),
                                 parser.read_en.eq(0)]
                with m.Else():
                    m.next = 'WAIT_INSTRUCTION'
                    m.d.sync += [polynomal.start.eq(1),
                                 parser.read_commit.eq(1),
                                 parser.read_en.eq(0)]
            # NOTE: system never recovers user must reset
            with m.State('ERROR'):
                m.next = 'ERROR'
        return m


# Overview:
#  the hardware consists out of the following elements
#  -- SPI command interface
#  -- transactionalized FIFO
#  -- SPI parser (basically an extension of SPI command interface)
#  -- Dispatcher --> dispatches signals to actual hardware
#  -- Polynomal integrator --> determines position via integrating counters

# TODO:

#   -- configure stepper drivers of motor
#   -- add tests for real hardware

#   -- luna splits modules over files and adds one test per file
#      this is probably cleaner than put all in one file approach
#   -- simulate blocking due to full memory during a move
#   -- verify homing procedure of controller
#   -- use CRC packet for tranmission failure (it is in litex but not luna)
#   -- try to replace value == 0 with ~value
#   -- the way word_bytes is counted in spi_parser is not clean
#   -- xfer3 is faster in transaction
#   -- if you chip select is released parsers should return to initial state
#   -- number of ticks per motor is uniform
#   -- code clones between testcontroller and controller is ugly
