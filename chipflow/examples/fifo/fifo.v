module fifo #(parameter WIDTH=8, DEPTH=4, ADDR=2)(
  input clk, input reset, input push, input pop, input [WIDTH-1:0] data_in,
  output [WIDTH-1:0] data_out, output empty, output full
);
  reg [ADDR-1:0] read_pointer, write_pointer;
  reg [ADDR:0] count;
  wire write_ok = push && !full;
  wire read_ok = pop && !empty;
  assign empty = count == 0;
  assign full = count == DEPTH;
  fifo_memory #(WIDTH,DEPTH,ADDR) storage(
    clk, write_ok && !reset, write_pointer, read_pointer, data_in, data_out
  );
  always @(posedge clk) begin
    if (reset) begin
      read_pointer <= 0; write_pointer <= 0; count <= 0;
    end else begin
      if (write_ok) begin
        write_pointer <= write_pointer + 1'b1;
      end
      if (read_ok) read_pointer <= read_pointer + 1'b1;
      case ({write_ok,read_ok})
        2'b10: count <= count + 1'b1;
        2'b01: count <= count - 1'b1;
        default: count <= count;
      endcase
    end
  end
endmodule
