"""Tests for SVD parser and register decoder."""

import os
import tempfile

import pytest

from server.svd.decoder import RegisterDecoder
from server.svd.parser import SVDParser, SVDDevice, SVDPeripheral, SVDRegister, SVDField

# Minimal SVD XML for testing
MINIMAL_SVD = """\
<?xml version="1.0" encoding="utf-8"?>
<device>
  <name>TestChip</name>
  <description>Test Device</description>
  <cpu>
    <name>CM4</name>
    <revision>r0p1</revision>
    <endian>little</endian>
    <nvicPrioBits>4</nvicPrioBits>
  </cpu>
  <peripherals>
    <peripheral>
      <name>SPI1</name>
      <description>Serial Peripheral Interface</description>
      <baseAddress>0x40013000</baseAddress>
      <registers>
        <register>
          <name>CR1</name>
          <description>Control register 1</description>
          <addressOffset>0x00</addressOffset>
          <size>32</size>
          <resetValue>0x00000000</resetValue>
          <fields>
            <field>
              <name>CPHA</name>
              <description>Clock phase</description>
              <bitOffset>0</bitOffset>
              <bitWidth>1</bitWidth>
              <enumeratedValues>
                <enumeratedValue><name>FirstEdge</name><value>0</value></enumeratedValue>
                <enumeratedValue><name>SecondEdge</name><value>1</value></enumeratedValue>
              </enumeratedValues>
            </field>
            <field>
              <name>CPOL</name>
              <description>Clock polarity</description>
              <bitOffset>1</bitOffset>
              <bitWidth>1</bitWidth>
              <enumeratedValues>
                <enumeratedValue><name>IdleLow</name><value>0</value></enumeratedValue>
                <enumeratedValue><name>IdleHigh</name><value>1</value></enumeratedValue>
              </enumeratedValues>
            </field>
            <field>
              <name>MSTR</name>
              <description>Master selection</description>
              <bitOffset>2</bitOffset>
              <bitWidth>1</bitWidth>
            </field>
            <field>
              <name>BR</name>
              <description>Baud rate control</description>
              <bitOffset>3</bitOffset>
              <bitWidth>3</bitWidth>
            </field>
            <field>
              <name>SPE</name>
              <description>SPI enable</description>
              <bitOffset>6</bitOffset>
              <bitWidth>1</bitWidth>
            </field>
            <field>
              <name>LSBFIRST</name>
              <description>Frame format</description>
              <bitOffset>7</bitOffset>
              <bitWidth>1</bitWidth>
            </field>
          </fields>
        </register>
        <register>
          <name>SR</name>
          <description>Status register</description>
          <addressOffset>0x08</addressOffset>
          <size>32</size>
          <resetValue>0x00000002</resetValue>
          <access>read-only</access>
          <fields>
            <field>
              <name>RXNE</name>
              <description>Receive buffer not empty</description>
              <bitOffset>0</bitOffset>
              <bitWidth>1</bitWidth>
            </field>
            <field>
              <name>TXE</name>
              <description>Transmit buffer empty</description>
              <bitOffset>1</bitOffset>
              <bitWidth>1</bitWidth>
            </field>
            <field>
              <name>OVR</name>
              <description>Overrun flag</description>
              <bitOffset>6</bitOffset>
              <bitWidth>1</bitWidth>
            </field>
            <field>
              <name>BSY</name>
              <description>Busy flag</description>
              <bitOffset>7</bitOffset>
              <bitWidth>1</bitWidth>
            </field>
          </fields>
        </register>
      </registers>
    </peripheral>
    <peripheral derivedFrom="SPI1">
      <name>SPI2</name>
      <description>Serial Peripheral Interface 2</description>
      <baseAddress>0x40003800</baseAddress>
    </peripheral>
  </peripherals>
</device>
"""

# Cortex-M SCB registers for fault analysis testing
SCB_SVD = """\
<?xml version="1.0" encoding="utf-8"?>
<device>
  <name>CortexM4_SCB</name>
  <peripherals>
    <peripheral>
      <name>SCB</name>
      <description>System Control Block</description>
      <baseAddress>0xE000ED00</baseAddress>
      <registers>
        <register>
          <name>CFSR</name>
          <description>Configurable Fault Status Register</description>
          <addressOffset>0x28</addressOffset>
          <size>32</size>
          <resetValue>0x00000000</resetValue>
          <fields>
            <field>
              <name>IACCVIOL</name>
              <description>Instruction access violation</description>
              <bitOffset>0</bitOffset>
              <bitWidth>1</bitWidth>
            </field>
            <field>
              <name>DACCVIOL</name>
              <description>Data access violation</description>
              <bitOffset>1</bitOffset>
              <bitWidth>1</bitWidth>
            </field>
            <field>
              <name>MUNSTKERR</name>
              <description>MemManage unstacking error</description>
              <bitOffset>3</bitOffset>
              <bitWidth>1</bitWidth>
            </field>
            <field>
              <name>MSTKERR</name>
              <description>MemManage stacking error</description>
              <bitOffset>4</bitOffset>
              <bitWidth>1</bitWidth>
            </field>
            <field>
              <name>MMARVALID</name>
              <description>MMFAR valid</description>
              <bitOffset>7</bitOffset>
              <bitWidth>1</bitWidth>
            </field>
            <field>
              <name>IBUSERR</name>
              <description>Instruction bus error</description>
              <bitOffset>8</bitOffset>
              <bitWidth>1</bitWidth>
            </field>
            <field>
              <name>PRECISERR</name>
              <description>Precise data bus error</description>
              <bitOffset>9</bitOffset>
              <bitWidth>1</bitWidth>
            </field>
            <field>
              <name>IMPRECISERR</name>
              <description>Imprecise data bus error</description>
              <bitOffset>10</bitOffset>
              <bitWidth>1</bitWidth>
            </field>
            <field>
              <name>UNSTKERR</name>
              <description>Unstacking bus error</description>
              <bitOffset>11</bitOffset>
              <bitWidth>1</bitWidth>
            </field>
            <field>
              <name>STKERR</name>
              <description>Stacking bus error</description>
              <bitOffset>12</bitOffset>
              <bitWidth>1</bitWidth>
            </field>
            <field>
              <name>BFARVALID</name>
              <description>BFAR valid</description>
              <bitOffset>15</bitOffset>
              <bitWidth>1</bitWidth>
            </field>
            <field>
              <name>UNDEFINSTR</name>
              <description>Undefined instruction</description>
              <bitOffset>16</bitOffset>
              <bitWidth>1</bitWidth>
            </field>
            <field>
              <name>INVSTATE</name>
              <description>Invalid state</description>
              <bitOffset>17</bitOffset>
              <bitWidth>1</bitWidth>
            </field>
            <field>
              <name>INVPC</name>
              <description>Invalid PC</description>
              <bitOffset>18</bitOffset>
              <bitWidth>1</bitWidth>
            </field>
            <field>
              <name>NOCP</name>
              <description>No coprocessor</description>
              <bitOffset>19</bitOffset>
              <bitWidth>1</bitWidth>
            </field>
            <field>
              <name>UNALIGNED</name>
              <description>Unaligned access</description>
              <bitOffset>24</bitOffset>
              <bitWidth>1</bitWidth>
            </field>
            <field>
              <name>DIVBYZERO</name>
              <description>Divide by zero</description>
              <bitOffset>25</bitOffset>
              <bitWidth>1</bitWidth>
            </field>
          </fields>
        </register>
      </registers>
    </peripheral>
  </peripherals>
</device>
"""


@pytest.fixture
def svd_file(tmp_path):
    f = tmp_path / "test.svd"
    f.write_text(MINIMAL_SVD)
    return str(f)


@pytest.fixture
def scb_svd_file(tmp_path):
    f = tmp_path / "scb.svd"
    f.write_text(SCB_SVD)
    return str(f)


@pytest.fixture
def parser():
    return SVDParser()


@pytest.fixture
def device(parser, svd_file):
    return parser.load(svd_file)


@pytest.fixture
def decoder(device):
    return RegisterDecoder(device)


class TestSVDParser:
    def test_device_name(self, device):
        assert device.name == "TestChip"

    def test_cpu_info(self, device):
        assert device.cpu.name == "CM4"
        assert device.cpu.nvic_priority_bits == 4

    def test_peripheral_count(self, device):
        assert len(device.peripherals) == 2
        assert "SPI1" in device.peripherals
        assert "SPI2" in device.peripherals

    def test_peripheral_base_address(self, device):
        assert device.peripherals["SPI1"].base_address == 0x40013000
        assert device.peripherals["SPI2"].base_address == 0x40003800

    def test_register_count(self, device):
        spi1 = device.peripherals["SPI1"]
        assert len(spi1.registers) == 2

    def test_register_fields(self, device):
        cr1 = device.peripherals["SPI1"].registers["CR1"]
        assert "CPOL" in cr1.fields
        assert "CPHA" in cr1.fields
        assert "BR" in cr1.fields

    def test_field_properties(self, device):
        cpol = device.peripherals["SPI1"].registers["CR1"].fields["CPOL"]
        assert cpol.bit_offset == 1
        assert cpol.bit_width == 1
        assert cpol.enumerated_values == {0: "IdleLow", 1: "IdleHigh"}

    def test_derived_peripheral(self, device):
        spi2 = device.peripherals["SPI2"]
        assert spi2.derived_from == "SPI1"
        # Should inherit registers
        assert len(spi2.registers) == 2
        assert "CR1" in spi2.registers

    def test_caching(self, parser, svd_file):
        d1 = parser.load(svd_file)
        d2 = parser.load(svd_file)
        assert d1 is d2  # Same object from cache


class TestRegisterDecoder:
    def test_decode_spi_cr1(self, decoder):
        # CPOL=1, CPHA=0, MSTR=1, BR=3 (0b011), SPE=1
        # Bits: SPE(6)=1, BR(5:3)=011, MSTR(2)=1, CPOL(1)=1, CPHA(0)=0
        # = 0b01011110 = 0x5E
        raw = 0x5E
        result = decoder.decode_register("SPI1", "CR1", raw)
        assert result.peripheral == "SPI1"
        assert result.register == "CR1"
        assert result.raw_value == 0x5E
        assert result.address == 0x40013000

        fields_by_name = {f.name: f for f in result.fields}
        assert fields_by_name["CPHA"].value == 0
        assert fields_by_name["CPOL"].value == 1
        assert fields_by_name["CPOL"].enumerated_name == "IdleHigh"
        assert fields_by_name["MSTR"].value == 1
        assert fields_by_name["BR"].value == 3
        assert fields_by_name["SPE"].value == 1

    def test_decode_cfsr_invstate(self, parser, scb_svd_file):
        device = parser.load(scb_svd_file)
        decoder = RegisterDecoder(device)
        # INVSTATE = bit 17 = 0x00020000
        result = decoder.decode_register("SCB", "CFSR", 0x00020000)
        fields_by_name = {f.name: f for f in result.fields}
        assert fields_by_name["INVSTATE"].value == 1
        assert fields_by_name["UNDEFINSTR"].value == 0

    def test_decode_by_address(self, decoder):
        result = decoder.decode_address(0x40013000, 0x04)  # SPI1 CR1
        assert result is not None
        assert result.peripheral == "SPI1"
        assert result.register == "CR1"

    def test_decode_unknown_address(self, decoder):
        result = decoder.decode_address(0xDEADBEEF, 0x00)
        assert result is None

    def test_encode_field_preserves_bits(self, decoder):
        # Start with CPOL=1, CPHA=0 (0x02)
        current = 0x02
        # Set CPHA=1 -- should become CPOL=1, CPHA=1 (0x03)
        new_value = decoder.encode_field_value("SPI1", "CR1", "CPHA", 1, current)
        assert new_value == 0x03

    def test_encode_field_clears_old(self, decoder):
        # Start with BR=7 (0x38)
        current = 0x38
        # Set BR=0 -- should clear bits
        new_value = decoder.encode_field_value("SPI1", "CR1", "BR", 0, current)
        assert new_value == 0x00

    def test_encode_multibit_field(self, decoder):
        # Set BR=5 (0b101) from 0
        new_value = decoder.encode_field_value("SPI1", "CR1", "BR", 5, 0x00)
        assert new_value == 0x28  # 5 << 3

    def test_list_peripherals(self, decoder):
        periphs = decoder.list_peripherals()
        assert "SPI1" in periphs
        assert "SPI2" in periphs

    def test_list_registers(self, decoder):
        regs = decoder.list_registers("SPI1")
        assert "CR1" in regs
        assert "SR" in regs

    def test_get_register_address(self, decoder):
        addr = decoder.get_register_address("SPI1", "CR1")
        assert addr == 0x40013000
        addr = decoder.get_register_address("SPI1", "SR")
        assert addr == 0x40013008

    def test_unknown_peripheral_raises(self, decoder):
        with pytest.raises(KeyError, match="Unknown peripheral"):
            decoder.decode_register("NOPE", "CR1", 0)

    def test_unknown_register_raises(self, decoder):
        with pytest.raises(KeyError, match="Unknown register"):
            decoder.decode_register("SPI1", "NOPE", 0)

    def test_status_register_overrun(self, decoder):
        # OVR = bit 6 = 0x40
        result = decoder.decode_register("SPI1", "SR", 0x42)
        fields_by_name = {f.name: f for f in result.fields}
        assert fields_by_name["TXE"].value == 1
        assert fields_by_name["OVR"].value == 1
        assert fields_by_name["BSY"].value == 0
